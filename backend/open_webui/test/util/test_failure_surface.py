"""Unit tests for the #16 runtime-slice backend detector/builder (failure_surface).

Loads the module BY FILE PATH so it runs with bare ``python3 -m unittest`` — no ``open_webui``
package import (which needs container deps), no live stack — mirroring the slice-1/part-4
``--self-test`` discipline. Pure-function coverage of the grilled decisions.
"""
import importlib.util
import logging
import os
import unittest

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
        self.assertEqual(fs.CAUSE_EMPTY_FINALIZED, "db_stream_flush")

    def test_payload_has_only_allow_listed_keys(self):
        p = fs.build_error_payload("deadbeefcafef00d", "0123456789abcdef")
        self.assertEqual(set(p), set(fs.ERROR_PAYLOAD_ALLOWED_KEYS))
        self.assertEqual(set(p), {"content", "cause", "trace_id"})

    def test_payload_carries_cause_and_trace_only_no_raw_content(self):
        p = fs.build_error_payload("deadbeefcafef00d", "0123456789abcdef")
        self.assertEqual(p["cause"], "db_stream_flush")
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
                                    "cause": "db_stream_flush", "trace_id": "t-a-b"})

    # ── canonical-cause fail-loud (Codex NO-PASS fix) ─────────────────────────
    def test_build_error_payload_rejects_non_canonical_cause(self):
        # the probe: an arbitrary/raw cause must be REJECTED, never echoed into error.cause
        with self.assertRaises(ValueError):
            fs.build_error_payload("chat1234", "msg45678", cause="RAW_CLIENT_MATTER_123")

    def test_build_error_payload_rejects_arbitrary_strings(self):
        for bad in ("", "ui_render_only_TYPO", "provider outage", "<script>", "db_stream_flush "):
            with self.subTest(cause=bad):
                with self.assertRaises(ValueError):
                    fs.build_error_payload("c1234567", "m1234567", cause=bad)

    def test_valid_canonical_cause_works(self):
        p = fs.build_error_payload("c1234567", "m1234567", cause=fs.CAUSE_EMPTY_FINALIZED)
        self.assertEqual(p["cause"], "db_stream_flush")
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
        # Single-sourcing-against-drift guard: the local ALLOWED_CAUSES must be a SUBSET of
        # scripts/ops/openwebui_8083_chat_triage.py CAUSES. Fails LOUD on divergence when the
        # parent-repo layout is reachable (host CI); skipped inside the owui container, which
        # cannot import the parent-repo ops script (hence this enforcement lives at test time).
        triage_path = os.path.normpath(os.path.join(
            _HERE, "..", "..", "..", "..", "..", "scripts", "ops", "openwebui_8083_chat_triage.py"))
        if not os.path.exists(triage_path):
            self.skipTest("parent-repo triage script not reachable from here")
        spec = importlib.util.spec_from_file_location("chat_triage", triage_path)
        triage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(triage)
        drift = set(fs.ALLOWED_CAUSES) - set(triage.CAUSES)
        self.assertEqual(drift, set(),
                         "ALLOWED_CAUSES drifted from triage.CAUSES: {}".format(sorted(drift)))


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
