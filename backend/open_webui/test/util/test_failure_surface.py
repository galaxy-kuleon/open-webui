"""Unit tests for the #16 runtime-slice backend detector/builder (failure_surface).

Loads the module BY FILE PATH so it runs with bare ``python3 -m unittest`` — no ``open_webui``
package import (which needs container deps), no live stack — mirroring the slice-1/part-4
``--self-test`` discipline. Pure-function coverage of the grilled decisions.
"""
import asyncio
import importlib.util
import logging
import os
import unittest

import pytest

_HERE = os.path.dirname(__file__)
_MOD = os.path.normpath(os.path.join(_HERE, "..", "..", "utils", "failure_surface.py"))
_spec = importlib.util.spec_from_file_location("failure_surface", _MOD)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

# A rendered tool/skip-rag turn is non-empty HTML (serialize_output renders tool blocks).
TOOL_RENDER = "<details><summary>Tool</summary>result</details>"


class TestShouldFlagEmpty(unittest.TestCase):
    # ── flags: truly-blank turns that have ended ──────────────────────────────
    def test_blank_and_done_true_flags(self):
        self.assertTrue(fs.should_flag_empty("", True, task_active=False))

    def test_blank_done_false_and_inactive_flags(self):
        # the done=false branch the backend CAN see (task ended, no content)
        self.assertTrue(fs.should_flag_empty("", False, task_active=False))

    def test_whitespace_only_is_treated_as_empty(self):
        self.assertTrue(fs.should_flag_empty("   \n\t ", True, task_active=False))

    # ── does NOT flag ─────────────────────────────────────────────────────────
    def test_non_empty_tool_or_skiprag_render_does_not_flag(self):
        self.assertFalse(fs.should_flag_empty(TOOL_RENDER, True, task_active=False))

    def test_non_empty_text_does_not_flag(self):
        self.assertFalse(fs.should_flag_empty("Here is your answer.", True, task_active=False))

    def test_blank_but_task_still_active_does_not_flag(self):
        # active task (e.g. live in another tab) → not a failure even if currently blank
        self.assertFalse(fs.should_flag_empty("", False, task_active=True))

    def test_cancel_with_partial_content_does_not_flag(self):
        # A5: a cancelled turn that kept partial content must NOT be clobbered/flagged
        self.assertFalse(fs.should_flag_empty("partial answer so far", True, task_active=False))


class TestTraceId(unittest.TestCase):
    def test_trace_format_and_8char_truncation(self):
        t = fs.make_trace_id("deadbeefcafef00d", "0123456789abcdef")
        self.assertEqual(t, "t-deadbeef-01234567")

    def test_trace_tolerates_short_or_missing_ids(self):
        self.assertEqual(fs.make_trace_id("abc", ""), "t-abc-")
        self.assertEqual(fs.make_trace_id(None, None), "t--")


class TestErrorPayload(unittest.TestCase):
    def test_default_cause_is_db_signal_label(self):
        # NOT "db_stream_flush" any more. That label said the answer completed
        # and the write did not -- which the finalizer never observed, because it
        # writes to the database FIRST and only then notices there is no
        # answerable output. This pins the label to the observation.
        self.assertEqual(fs.CAUSE_EMPTY_FINALIZED, "finalized_no_answer")
        self.assertNotIn("db_stream_flush", fs.ALLOWED_CAUSES)

    def test_payload_has_only_allow_listed_keys(self):
        p = fs.build_error_payload("deadbeefcafef00d", "0123456789abcdef")
        self.assertEqual(set(p), set(fs.ERROR_PAYLOAD_ALLOWED_KEYS))
        self.assertEqual(set(p), {"content", "cause", "trace_id"})

    def test_payload_carries_cause_and_trace_only_no_raw_content(self):
        p = fs.build_error_payload("deadbeefcafef00d", "0123456789abcdef")
        self.assertEqual(p["cause"], "finalized_no_answer")
        self.assertEqual(p["trace_id"], "t-deadbeef-01234567")
        # content is exactly the fixed banner from cause+trace — nothing else can be in it
        self.assertEqual(p["content"], fs.build_banner(p["cause"], p["trace_id"]))
        self.assertIn(p["trace_id"], p["content"])
        self.assertIn(p["cause"], p["content"])

    def test_builder_cannot_carry_injected_raw_content(self):
        # there is no content/title parameter; a probe string cannot reach the payload
        p = fs.build_error_payload("chatWITHsecretNAME", "msgWITHsecret")
        # ids are truncated to 8 and only ids/cause appear; assert no full probe leaks
        self.assertNotIn("secretNAME", p["content"])
        self.assertNotIn("WITHsecret", p["content"])

    def test_assert_privacy_safe_rejects_extra_key(self):
        with self.assertRaises(ValueError):
            fs.assert_privacy_safe({"content": fs.build_banner("c", "t"), "cause": "c",
                                    "trace_id": "t", "raw": "leak"})

    def test_assert_privacy_safe_rejects_non_string_value(self):
        with self.assertRaises(ValueError):
            fs.assert_privacy_safe({"content": "x", "cause": "c", "trace_id": 123})

    def test_assert_privacy_safe_rejects_tampered_banner(self):
        # a content not reconstructible from cause+trace (e.g. raw content spliced in) → fail
        with self.assertRaises(ValueError):
            fs.assert_privacy_safe({"content": "Secret client matter text",
                                    "cause": "finalized_no_answer", "trace_id": "t-a-b"})

    # ── canonical-cause fail-loud (Codex NO-PASS fix) ─────────────────────────
    def test_build_error_payload_rejects_non_canonical_cause(self):
        # the probe: an arbitrary/raw cause must be REJECTED, never echoed into error.cause
        with self.assertRaises(ValueError):
            fs.build_error_payload("chat1234", "msg45678", cause="RAW_CLIENT_MATTER_123")

    def test_build_error_payload_rejects_arbitrary_strings(self):
        for bad in ("", "ui_render_only_TYPO", "provider outage", "<script>", "finalized_no_answer "):
            with self.subTest(cause=bad):
                with self.assertRaises(ValueError):
                    fs.build_error_payload("c1234567", "m1234567", cause=bad)

    def test_valid_canonical_cause_works(self):
        p = fs.build_error_payload("c1234567", "m1234567", cause=fs.CAUSE_EMPTY_FINALIZED)
        self.assertEqual(p["cause"], "finalized_no_answer")
        self.assertIn(p["cause"], fs.ALLOWED_CAUSES)

    def test_assert_privacy_safe_rejects_non_canonical_cause(self):
        # defense-in-depth: even a well-formed banner with a non-canonical cause is rejected
        bad_cause = "RAW_CLIENT_MATTER_123"
        with self.assertRaises(ValueError):
            fs.assert_privacy_safe({
                "content": fs.build_banner(bad_cause, "t-a-b"),
                "cause": bad_cause,
                "trace_id": "t-a-b",
            })

    def test_allowed_causes_is_defined_and_canonical(self):
        self.assertTrue(fs.ALLOWED_CAUSES)  # non-empty
        self.assertIn(fs.CAUSE_EMPTY_FINALIZED, fs.ALLOWED_CAUSES)
        self.assertTrue(all(isinstance(c, str) and c for c in fs.ALLOWED_CAUSES))


class TestVocabularySingleSourced(unittest.TestCase):
    def test_allowed_causes_is_subset_of_triage_CAUSES_when_reachable(self):
        # Single-sourcing-against-drift guard: every label this module may emit must appear
        # in the parent repo's declared vocabulary. Fails LOUD on divergence when the
        # parent-repo layout is reachable (host CI); skipped inside the owui container, which
        # cannot import the parent-repo ops script (hence this enforcement lives at test time).
        #
        # AGAINST THE UNION OF BOTH PRODUCER LISTS, not against `CAUSES` alone. `CAUSES` is
        # the triage CLASSIFIER's output set, and that module's own self-test requires every
        # member of it to be returnable by `classify()`. A label this module emits and that
        # classifier never returns therefore cannot live there: as a member it is decorative
        # coverage, and as an omission it is drift here. The two invariants contradicted the
        # moment a second producer existed. One list per producer, one union for readers.
        triage_path = os.path.normpath(os.path.join(
            _HERE, "..", "..", "..", "..", "..", "scripts", "ops", "openwebui_8083_chat_triage.py"))
        if not os.path.exists(triage_path):
            self.skipTest("parent-repo triage script not reachable from here")
        spec = importlib.util.spec_from_file_location("chat_triage", triage_path)
        triage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(triage)
        declared = set(triage.CAUSES) | set(getattr(triage, "PRODUCER_CAUSES", ()))
        drift = set(fs.ALLOWED_CAUSES) - declared
        self.assertEqual(drift, set(),
                         "ALLOWED_CAUSES drifted from the declared vocabulary: {}".format(
                             sorted(drift)))
        # ...and the reverse for THIS producer's own list: a label declared as ours that we
        # cannot emit is the same decorative coverage, one file over.
        stale = set(getattr(triage, "PRODUCER_CAUSES", ())) - set(fs.ALLOWED_CAUSES)
        self.assertEqual(stale, set(),
                         "PRODUCER_CAUSES lists labels failure_surface cannot emit: {}".format(
                             sorted(stale)))



class TestCauseMatchesPhase(unittest.TestCase):
    """An empty turn's cause must describe how it ended.

    Reproduced live on 8083 before this existed: the interrupted branch logged
    `phase=interrupted` two statements after building a banner that took the
    finalized default, so the user was told "cause: db_stream_flush" for a turn
    they had stopped. The phase was known the whole time; the banner had no
    vocabulary for it, and one deletable default is all it took.
    """

    def test_each_phase_has_its_own_cause_and_they_differ(self):
        interrupted = fs.cause_for_phase(fs.PHASE_INTERRUPTED)
        finalized = fs.cause_for_phase(fs.PHASE_FINALIZED)
        self.assertEqual(fs.CAUSE_EMPTY_INTERRUPTED, interrupted)
        self.assertEqual(fs.CAUSE_EMPTY_FINALIZED, finalized)
        self.assertNotEqual(
            interrupted, finalized,
            "an interrupted turn and a finalized-but-empty turn are different "
            "failures and must not render the same cause")

    def test_every_allowed_phase_maps_to_an_allowed_cause(self):
        for phase in fs.ALLOWED_PHASES:
            self.assertIn(fs.cause_for_phase(phase), fs.ALLOWED_CAUSES, phase)

    def test_an_unknown_phase_says_unknown_instead_of_borrowing(self):
        # A banner is the last thing between a user and a blank box; it must not
        # be the thing that fails -- but it must not invent a diagnosis either.
        # This used to return the FINALIZED cause, so "we could not tell which
        # boundary this was" reached the user as a specific claim about one of
        # them.
        self.assertEqual(fs.CAUSE_EMPTY_UNKNOWN, fs.cause_for_phase("nonsense"))
        self.assertNotEqual(fs.CAUSE_EMPTY_FINALIZED, fs.cause_for_phase("nonsense"))

    def test_the_banner_no_longer_claims_what_reached_the_browser(self):
        # "Nothing was delivered" was never observed: reasoning, status and tool
        # markup may already be on screen, and `await sio.emit` is emit
        # acceptance, not a browser acknowledgement.
        banner = fs.build_banner(fs.CAUSE_EMPTY_FINALIZED, "t-a-b")
        self.assertNotIn("Nothing was delivered", banner)
        self.assertIn("without a final answer", banner)
        self.assertIn("t-a-b", banner)

    def test_the_banner_carries_the_interrupted_cause_end_to_end(self):
        payload = fs.build_error_payload(
            "chat-1234", "msg-5678", fs.cause_for_phase(fs.PHASE_INTERRUPTED))
        self.assertEqual(fs.CAUSE_EMPTY_INTERRUPTED, payload["cause"])
        self.assertIn(fs.CAUSE_EMPTY_INTERRUPTED, payload["content"])
        self.assertNotIn(fs.CAUSE_EMPTY_FINALIZED, payload["content"])
        fs.assert_privacy_safe(payload)


class TestMiddlewareDerivesCauseFromPhase(unittest.TestCase):
    """The two empty-turn sites must each pass THEIR OWN phase.

    Source-level on purpose, and honest about it: driving the middleware's
    empty-turn branches needs a full request pipeline, while the defect this
    guards is textual -- one site logging PHASE_INTERRUPTED while building its
    banner from the finalized default. That drift is exactly what a source
    assertion can see, and it is what actually happened.
    """

    MIDDLEWARE = os.path.normpath(os.path.join(
        _HERE, "..", "..", "utils", "middleware.py"))

    def _source(self):
        with open(self.MIDDLEWARE, encoding="utf-8") as fh:
            return fh.read()

    def test_no_empty_turn_banner_takes_the_default_cause(self):
        src = self._source()
        # Every build_error_payload call in the empty-turn paths must name a
        # cause. A bare two-argument call silently inherits the finalized
        # default, which is the bug.
        bare = src.count("build_error_payload(\n"
                         "                            metadata['chat_id'], "
                         "metadata['message_id']\n"
                         "                        )")
        self.assertEqual(
            0, bare,
            "an empty-turn banner is taking the DEFAULT cause instead of "
            "deriving it from its phase")

    def test_each_phase_appears_with_its_own_derivation(self):
        src = self._source()
        self.assertIn("cause_for_phase(PHASE_INTERRUPTED)", src)
        self.assertIn("cause_for_phase(PHASE_FINALIZED)", src)

    def test_the_interrupted_site_does_not_derive_the_finalized_cause(self):
        """The precise regression: the interrupted branch quietly using the
        finalized cause. Anchored on the marker call that names the phase."""
        src = self._source()
        i = src.find("cause_for_phase(PHASE_INTERRUPTED)")
        self.assertGreater(i, 0)
        # ...and the marker logged in that same block says INTERRUPTED too, so
        # the banner and the marker cannot disagree about one turn again.
        block = src[i:i + 2000]
        self.assertIn("PHASE_INTERRUPTED", block.split("log_empty_turn", 1)[-1][:200])

class TurnOpenedMarkerTests(unittest.TestCase):
    """THE DEFECT: a turn that dies before the finalizer left no trace at all.

    Measured on live 8083 on 2026-08-13 -- nine blank assistant turns since the
    ledger began, five with no cause anywhere: `done=False`, no error on the row,
    no ledger record of any kind. Every marker this module emitted was written by
    the finalizer, so the turns that never reached it were invisible by
    construction, and their absence read as "nothing happened".
    """

    def test_the_marker_names_the_turn_it_opened(self):
        line = fs.build_turn_opened_marker("chat-aaaaaaaa", "msg-bbbbbbbb", "hermes-agent")

        self.assertEqual(
            line,
            "turn_opened service=owui chat=chat-aaaaaaaa msg=msg-bbbbbbbb "
            "model=hermes-agent",
        )

    def test_it_shares_the_join_key_with_the_empty_turn_marker(self):
        # Without this the two halves of a lifecycle cannot be paired, and
        # "opened but never terminated" stays uncomputable.
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg-bbbbbbbb")
        closed = fs.build_marker(payload, fs.PHASE_FINALIZED, "chat-aaaaaaaa",
                                 "msg-bbbbbbbb", fs.NOTICE_WRITTEN)
        opened = fs.build_turn_opened_marker("chat-aaaaaaaa", "msg-bbbbbbbb", "m")

        for field in ("chat=chat-aaaaaaaa", "msg=msg-bbbbbbbb"):
            self.assertIn(field, opened)
            self.assertIn(field, closed)

    def test_whitespace_would_silently_truncate_the_record(self):
        # `_KV_RE` in journey_ledger splits on whitespace: a model id with a
        # space would file half a record and look like a complete one.
        with self.assertRaises(ValueError):
            fs.build_turn_opened_marker("chat-a", "msg-b", "two words")

    def test_a_broken_log_sink_cannot_break_the_turn(self):
        log = logging.getLogger("open_webui.utils.failure_surface")

        class Exploding(logging.Handler):
            def emit(self, record):
                raise RuntimeError("sink is down")

        h = Exploding()
        log.addHandler(h)
        try:
            line = fs.log_turn_opened("chat-aaaaaaaa", "msg-bbbbbbbb", "m")
        finally:
            log.removeHandler(h)
        self.assertIn("turn_opened service=owui", line)

    def test_an_unbuildable_marker_is_still_emitted(self):
        """Opposite direction from the empty-turn marker, on purpose.

        A missing empty-turn marker hides a failure. A missing `turn_opened`
        hides the turn's EXISTENCE, which turns an unterminated turn back into
        one that looks like it never started -- exactly the confusion this
        marker exists to end. So a bad field drops the ids and still records
        that a turn began.
        """
        line = fs.log_turn_opened("chat-a", "msg-b", "two words")

        self.assertTrue(line.startswith("turn_opened service=owui"))
        self.assertIn("err=ValueError", line)
        self.assertIn("chat=- msg=-", line)


if __name__ == "__main__":
    unittest.main()


class MarkerEmissionIsBestEffortTests(unittest.TestCase):
    """The emitter must not be able to break the turn it is describing.

    Adversarial review round 28 proved the opposite with this exact probe: only
    marker CONSTRUCTION was guarded, so a handler whose `emit()` raises escaped
    into the empty-turn finalizer -- the one path whose whole job is rescuing a
    turn that already went wrong.
    """

    def _with_broken_sink(self, fn):
        log = logging.getLogger("open_webui.utils.failure_surface")

        class _Exploding(logging.Handler):
            def emit(self, record):
                raise RuntimeError("synthetic sink failure")

        h = _Exploding()
        log.addHandler(h)
        log.setLevel(logging.INFO)
        try:
            return fn()
        finally:
            log.removeHandler(h)

    def test_a_broken_log_sink_cannot_break_the_turn(self):
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg-bbbbbbbb")
        line = self._with_broken_sink(
            lambda: fs.log_empty_turn(payload, fs.PHASE_FINALIZED,
                                      "chat-aaaaaaaa", "msg-bbbbbbbb",
                                      fs.NOTICE_WRITTEN))
        self.assertIn("empty_reply service=owui", line,
                      "the marker was not even built")

    def test_an_unbuildable_marker_still_returns_and_says_so(self):
        """A bad phase must not raise either -- and must not claim a trace."""
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg-bbbbbbbb")
        line = fs.log_empty_turn(payload, "not-a-phase",
                                 "chat-aaaaaaaa", "msg-bbbbbbbb",
                                 fs.NOTICE_WRITTEN)
        self.assertIn("phase=marker_unbuildable", line)
        self.assertIn("notice=unknown", line,
                      "an unbuildable marker claimed the user was told")
        self.assertIn("trace=-", line,
                      "an unresolvable row advertised a trace id")
        # M4: the exception must contribute a CLASS NAME and nothing else.
        self.assertIn("err=ValueError", line)

    def test_a_whitespace_bearing_id_cannot_truncate_the_record(self):
        """`_KV_RE` in the collector splits on whitespace, so a space inside an
        id would silently drop every field after it. Message ids arrive from the
        caller, so this is reachable input, not a hypothetical."""
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg bbbbbbbb")
        line = fs.log_empty_turn(payload, fs.PHASE_FINALIZED,
                                 "chat-aaaaaaaa", "msg bbbbbbbb",
                                 fs.NOTICE_WRITTEN)
        self.assertIn("phase=marker_unbuildable", line,
                      f"a whitespace-bearing id produced a normal record: {line}")


class FailureKindFromTypeTests(unittest.TestCase):
    """The producer-owned enum round 33 asked for, tested at its own boundary."""

    @staticmethod
    def _exc(module, name, base=Exception):
        """An exception standing in for a real library class, module and all.

        The module half is the point. These tests used to declare LOCAL classes
        called `TransferEncodingError` and then assert they classified -- which
        made the suite agree with the defect: bare-name matching accepted any
        foreign class that shared a name. Round 36 built that collision
        deliberately, and these tests would have blessed it.
        """
        klass = type(name, (base,), {})
        klass.__module__ = module
        return klass

    def test_the_real_library_classes_classify(self):
        for module, name, want in (
            ("aiohttp.http_exceptions", "TransferEncodingError", "stream_truncated"),
            ("aiohttp.http_exceptions", "LineTooLong", "sse_line_too_long"),
            ("aiohttp.client_exceptions", "ServerDisconnectedError", "peer_disconnected"),
            ("httpx", "RemoteProtocolError", "stream_truncated"),
            ("http.client", "IncompleteRead", "stream_truncated"),
        ):
            with self.subTest(cls=f"{module}.{name}"):
                self.assertEqual(want, fs.classify_exception(
                    self._exc(module, name)()))

    def test_builtin_timeouts_classify(self):
        self.assertEqual("upstream_timeout", fs.classify_exception(TimeoutError()))
        self.assertEqual("peer_disconnected",
                         fs.classify_exception(ConnectionResetError()))

    def test_a_subclass_still_classifies(self):
        """aiohttp and httpx both subclass their own base errors."""
        base = self._exc("aiohttp.http_exceptions", "TransferEncodingError")
        vendor = type("VendorSpecific", (base,), {})
        vendor.__module__ = "some.vendor.shim"
        self.assertEqual("stream_truncated", fs.classify_exception(vendor()))

    def test_a_FOREIGN_class_with_a_known_NAME_does_not_classify(self):
        """Round 36's collision, as a standing control.

        An unrelated `TransferEncodingError` from a business-rule module is not a
        transport truncation. Bare-name matching said it was, without reading a
        single character of any message.
        """
        self.assertEqual("unclassified", fs.classify_exception(
            self._exc("synthetic.business_rule", "TransferEncodingError")()))
        self.assertEqual("unclassified", fs.classify_exception(
            self._exc("synthetic.cache", "TimeoutError")()))

    def test_an_unknown_type_is_unclassified_not_guessed(self):
        class SomethingNew(Exception):
            pass

        self.assertEqual("unclassified", fs.classify_exception(SomethingNew()))

    def test_the_MESSAGE_can_never_classify(self):
        """The whole reason this exists.

        A ValueError whose text quotes a truncation is not a truncation. Text
        matching both misses real failures spelled differently and lets a chat
        ABOUT an outage manufacture one; classifying on the type cannot do
        either.
        """
        self.assertEqual(
            "unclassified",
            fs.classify_exception(ValueError(
                "Response payload is not completed: <TransferEncodingError: 400, "
                "message='Not enough data to satisfy transfer length'>")))

    def test_the_marker_carries_it_and_refuses_a_non_canonical_one(self):
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg-bbbbbbbb")
        line = fs.build_marker(payload, fs.PHASE_INTERRUPTED, "chat-aaaaaaaa",
                               "msg-bbbbbbbb", fs.NOTICE_WRITTEN,
                               "stream_truncated")
        self.assertIn("failure=stream_truncated", line)
        with self.assertRaises(ValueError):
            fs.build_marker(payload, fs.PHASE_INTERRUPTED, "chat-aaaaaaaa",
                            "msg-bbbbbbbb", fs.NOTICE_WRITTEN, "made up")

    def test_default_is_unclassified_so_an_unwired_caller_cannot_claim_a_cause(self):
        payload = fs.build_error_payload("chat-aaaaaaaa", "msg-bbbbbbbb")
        line = fs.build_marker(payload, fs.PHASE_FINALIZED, "chat-aaaaaaaa",
                               "msg-bbbbbbbb", fs.NOTICE_WRITTEN)
        self.assertIn("failure=unclassified", line)


class ReadBoundaryOwnsTheFactTests(unittest.TestCase):
    """Only a failure raised WHILE READING the body may be called a transport failure.

    Round 36 traced a `TimeoutError` from the final `event_emitter` flush through
    the whole-handler catch and out as `upstream_timeout` — a closed,
    privacy-safe, causally FALSE label that would send an operator to stare at a
    provider which had answered perfectly.
    """

    @staticmethod
    def _run(agen):
        async def drain():
            out = []
            async for chunk in agen:
                out.append(chunk)
            return out
        return asyncio.run(drain())

    def test_a_clean_body_passes_through_untouched(self):
        async def body():
            for chunk in (b"a", b"b", b"c"):
                yield chunk
        self.assertEqual([b"a", b"b", b"c"],
                         self._run(fs.classified_body_reads(body())))

    def test_a_read_failure_is_classified_and_typed(self):
        klass = type("TransferEncodingError", (Exception,), {})
        klass.__module__ = "aiohttp.http_exceptions"

        async def body():
            yield b"partial"
            raise klass()

        with self.assertRaises(fs.StreamReadFailure) as caught:
            self._run(fs.classified_body_reads(body()))
        self.assertEqual("stream_truncated", caught.exception.failure_kind)
        self.assertIsInstance(caught.exception.__cause__, klass)

    def test_a_read_failure_of_an_unknown_type_is_unclassified(self):
        async def body():
            raise ValueError("Response payload is not completed: TransferEncodingError")
            yield  # pragma: no cover

        with self.assertRaises(fs.StreamReadFailure) as caught:
            self._run(fs.classified_body_reads(body()))
        self.assertEqual("unclassified", caught.exception.failure_kind)

    def test_StopAsyncIteration_is_an_ending_not_a_failure(self):
        async def body():
            return
            yield  # pragma: no cover
        self.assertEqual([], self._run(fs.classified_body_reads(body())))


class RealLibraryClassesTests(unittest.TestCase):
    """The ACTUAL installed exception classes, imported.

    The tests above hand-author stand-ins with the right `__module__`, which
    proves the mapping SHAPE and nothing about the dependencies. Round 37: that
    test can agree with a future wrong guess exactly as the previous local-class
    test agreed with bare-name matching. These import the real classes, so a
    library moving one between submodules turns this red instead of silently
    reclassifying live failures as `unclassified`.

    Skipped rather than failed when a dependency is absent: this suite also runs
    on a bare host, and a missing optional library is not a mapping defect.
    """

    def test_aiohttp_transport_errors(self):
        """Imported from the SUBMODULES that define them.

        My first version used `getattr(aiohttp, name)` and skipped when it
        returned None — and `TransferEncodingError` is not exported at
        aiohttp's top level, so the test reported SKIPPED and I read it as
        covered. Absence taken for success, in the test written to stop exactly
        that. A missing class is now a FAILURE: it means the library moved
        something and live failures would be silently reclassified.
        """
        he = pytest.importorskip("aiohttp.http_exceptions")
        ce = pytest.importorskip("aiohttp.client_exceptions")
        for mod, name, want in (
            (he, "TransferEncodingError", "stream_truncated"),
            (he, "ContentLengthError", "stream_truncated"),
            (he, "LineTooLong", "sse_line_too_long"),
            (ce, "ClientPayloadError", "stream_truncated"),
            (ce, "ServerDisconnectedError", "peer_disconnected"),
            (ce, "ClientConnectionError", "peer_disconnected"),
            (ce, "ServerTimeoutError", "upstream_timeout"),
        ):
            klass = getattr(mod, name, None)
            self.assertIsNotNone(
                klass, f"{mod.__name__}.{name} no longer exists — the mapping "
                       f"is stale and real failures would classify as unclassified")
            with self.subTest(cls=f"{klass.__module__}.{klass.__qualname__}"):
                self.assertEqual(want, fs.classify_exception(klass.__new__(klass)))

    def test_stdlib_incomplete_read(self):
        import http.client
        self.assertEqual("stream_truncated",
                         fs.classify_exception(http.client.IncompleteRead(b"")))

    def test_builtin_timeout_and_reset(self):
        self.assertEqual("upstream_timeout", fs.classify_exception(TimeoutError()))
        self.assertEqual("peer_disconnected",
                         fs.classify_exception(ConnectionResetError()))


class NoticeMustBeObservedTests(unittest.TestCase):
    """`notice=written` may not be claimed on an unwritten row.

    Both empty-turn sites awaited the upsert and DISCARDED its result, then set
    NOTICE_WRITTEN. `Chats.upsert_message_to_chat_by_id_and_message_id` returns
    None for a chat that is gone and raises nothing, so "the await finished" was
    never proof the banner persisted -- and every `notice=written` on the ledger
    was an unchecked claim. The partial-answer path one screen up has always
    tested this; these two did not.

    Checked as source structure because the sites live inside a 6000-line async
    handler that cannot be imported here. Weaker than executing them, and it is
    the check that would have caught the defect.
    """

    def _sites(self):
        import os
        import re

        path = os.path.normpath(os.path.join(
            _HERE, "..", "..", "utils", "middleware.py"))
        src = open(path, encoding="utf-8").read()
        return src, [m.start() for m in re.finditer(
            r"notice = NOTICE_WRITTEN", src)]

    def test_every_notice_written_is_guarded_by_the_upsert_result(self):
        src, sites = self._sites()
        self.assertTrue(sites, "no NOTICE_WRITTEN assignment found at all")
        for start in sites:
            window = src[max(0, start - 1200):start]
            self.assertIn("surfaced = await Chats.upsert_message", window)
            self.assertIn("if surfaced is not None:", src[start - 200:start])

    def test_the_default_stays_undelivered(self):
        # The safe direction: a reader treats an unknown/undelivered notice as
        # not-explained. A default of WRITTEN would make a failed surface read
        # as a user who was told.
        src, _ = self._sites()
        self.assertIn("notice = NOTICE_UNDELIVERED", src)


class ChannelInterruptionIsNotARescueFailureTests(unittest.TestCase):
    """`no` must mean the rescue was attempted and did not land.

    A channel turn has no persistence path — middleware skips the write for
    `channel:` chats. Reporting `no` there manufactures a data-loss incident out
    of a design decision, and every channel interruption reads as lost text.

    This shipped half-done once: the three states existed in the builder while
    the only caller still passed `bool(persisted)`, so `n/a` was unreachable on
    the wire. Fixing the vocabulary without fixing its caller fixed nothing.
    """

    def test_a_channel_interruption_is_not_a_rescue_failure(self):
        import ast
        import os
        src_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "utils", "middleware.py"))
        src = open(src_path, encoding="utf-8").read()

        # The caller must START from not-applicable and narrow to a real boolean
        # only on the path that actually attempts a write.
        self.assertIn("persisted = PERSISTED_NOT_APPLICABLE", src)

        # ...and must not collapse the three states back to two at the call.
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "log_turn_interrupted"):
                for arg in node.args:
                    self.assertNotEqual(
                        getattr(getattr(arg, "func", None), "id", None), "bool",
                        "bool() at the call site makes `n/a` unreachable")
