"""Behavioural cover for the interrupted-turn save.

The structural suite next door proves the SHAPE — that a boundary exists, that
both branches reach it, that the write precedes the notification. It cannot
prove what the function does when a collaborator misbehaves, and that is where
the last defect lived: `Chats.upsert_message_to_chat_by_id_and_message_id`
returns `None` for a chat that is gone (deleted between stream start and
interruption, or losing a delete race) and raises nothing at all. The code set
`persisted = True` regardless and logged `persisted=yes` over a row that was
never written.

So this executes the real nested function, lifted from the real source, against
synthetic collaborators. No live service, no database, no network.
"""

import ast
import asyncio
import importlib.util
import logging
import textwrap
import unittest
from pathlib import Path

MIDDLEWARE = Path(__file__).resolve().parents[2] / "utils" / "middleware.py"

# The REAL producer, not a stub. `failure_surface` is pure by design (no
# `open_webui` imports), so it loads by path with no app and no stack -- and the
# marker it emits is the only record of an empty turn that outlives the
# container, so a stub here would test nothing that matters.
_FS_PATH = Path(__file__).resolve().parents[2] / "utils" / "failure_surface.py"
_fs_spec = importlib.util.spec_from_file_location("_fs_under_test", _FS_PATH)
failure_surface = importlib.util.module_from_spec(_fs_spec)
_fs_spec.loader.exec_module(failure_surface)


def _lift(name):
    """Compile one nested function out of middleware.py with no imports."""
    src = MIDDLEWARE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == name)
    return textwrap.dedent(
        "".join(src.splitlines(True)[fn.lineno - 1:fn.end_lineno]))


class _Chats:
    """Stands in for the real model layer."""

    def __init__(self, returns):
        self.returns = list(returns)
        self.calls = []

    async def upsert_message_to_chat_by_id_and_message_id(self, chat, msg, patch):
        self.calls.append(patch)
        return self.returns.pop(0) if self.returns else {"ok": True}


class _Log:
    def __init__(self):
        self.lines = []

    def _add(self, fmt, *a):
        self.lines.append(fmt % a if a else fmt)

    info = warning = exception = _add


def _run(chats_returns, output_items, emitter_raises=False):
    log = _Log()
    chats = _Chats(chats_returns)
    events = []

    async def event_emitter(evt):
        events.append(evt.get("type"))
        if emitter_raises:
            raise RuntimeError("socket.io is down")

    def serialize_output(items):
        out = []
        for i in items:
            for part in i.get("content", []) or []:
                out.append(part.get("text", ""))
        return "".join(out)

    ns = {
        "asyncio": asyncio, "json": __import__("json"),
        "Chats": chats, "log": log, "event_emitter": event_emitter,
        "serialize_output": serialize_output,
        "full_output": lambda: output_items,
        "answerable_output": lambda items=None: [
            i for i in (output_items if items is None else items)
            if i.get("type") != "reasoning"],
        "ENABLE_REALTIME_CHAT_SAVE": False,
        "metadata": {"chat_id": "c-1", "message_id": "m-1"},
        "should_flag_empty": lambda s, *a, **k: not (s or "").strip(),
        # The real builder and the real emitter. The payload the banner is made
        # from and the marker ops greps for are the same two objects here as in
        # production, so a change that breaks either is a red test rather than a
        # blank screen with an unresolvable trace id.
        "build_error_payload": failure_surface.build_error_payload,
        # The lifted block derives its banner cause from its phase, so the
        # lifted namespace has to carry the derivation too. Without it the
        # block raises NameError and every test here fails for a reason that
        # has nothing to do with what it is testing.
        "cause_for_phase": failure_surface.cause_for_phase,
        "PHASE_FINALIZED": failure_surface.PHASE_FINALIZED,
        "log_empty_turn": failure_surface.log_empty_turn,
        "PHASE_INTERRUPTED": failure_surface.PHASE_INTERRUPTED,
        "PHASE_FINALIZED": failure_surface.PHASE_FINALIZED,
        "NOTICE_WRITTEN": failure_surface.NOTICE_WRITTEN,
        "NOTICE_UNDELIVERED": failure_surface.NOTICE_UNDELIVERED,
        "FAILURE_KIND_UNCLASSIFIED": failure_surface.FAILURE_KIND_UNCLASSIFIED,
        "ALLOWED_FAILURE_KINDS": failure_surface.ALLOWED_FAILURE_KINDS,
        "classify_exception": failure_surface.classify_exception,
    }
    marker_lines = []
    handler = logging.Handler()
    handler.emit = lambda rec: marker_lines.append(rec.getMessage())
    marker_log = logging.getLogger("open_webui.utils.failure_surface")
    marker_log.addHandler(handler)
    marker_log.setLevel(logging.INFO)
    try:
        exec(compile(_lift("save_interrupted_state"), "<lifted>", "exec"), ns)
        asyncio.run(ns["save_interrupted_state"]("stream_failed"))
    finally:
        marker_log.removeHandler(handler)
    log.markers = marker_lines
    return log, chats, events


ANSWER = [{"type": "message", "status": "completed",
           "content": [{"type": "output_text", "text": "half an answer"}]}]
THINKING_ONLY = [{"type": "reasoning", "status": "in_progress",
                  "content": [{"type": "output_text", "text": "thinking"}]}]


class InterruptedSaveBehaviourTests(unittest.TestCase):
    def test_a_chat_that_is_gone_is_never_reported_as_persisted(self):
        """The real API returns None for a missing chat and raises nothing."""
        log, chats, _ = _run([None], ANSWER)
        self.assertTrue(chats.calls, "it did not even try to write")
        self.assertTrue(
            any("persisted=no" in l for l in log.lines),
            f"claimed the text reached disk when no row was written: "
            f"{log.lines}")
        self.assertTrue(
            any("persist_failed" in l for l in log.lines),
            "a write that stored nothing passed silently")

    def test_a_real_write_is_reported_as_persisted(self):
        log, chats, _ = _run([{"id": "c-1"}], ANSWER)
        self.assertTrue(any("persisted=yes" in l for l in log.lines), log.lines)
        self.assertFalse(any("persist_failed" in l for l in log.lines))

    def test_a_dead_socket_cannot_cost_the_user_their_text(self):
        log, chats, events = _run([{"id": "c-1"}], ANSWER, emitter_raises=True)
        self.assertTrue(chats.calls,
                        "the notification failed and took the write with it")
        self.assertIn("content", chats.calls[0],
                      "the first write did not carry the user's text")

    def test_the_spinner_is_closed_in_what_gets_saved(self):
        items = [dict(i) for i in THINKING_ONLY]
        _run([{"id": "c-1"}], items)
        self.assertEqual(items[0]["status"], "completed",
                         "saved state still says the turn is thinking")

    def test_a_thinking_only_turn_is_still_reported_empty(self):
        """The B2 correction must not have silenced the real empty case."""
        log, chats, events = _run([{"id": "c-1"}, {"id": "c-1"}],
                                  [dict(i) for i in THINKING_ONLY])
        self.assertIn("chat:message:error", events,
                      "a turn that only thought was not surfaced as answerless")

    def test_a_real_answer_is_not_reported_empty(self):
        log, chats, events = _run([{"id": "c-1"}], ANSWER)
        self.assertNotIn("chat:message:error", events,
                         "a recovered partial answer got a blank-screen banner")
        self.assertEqual([], log.markers,
                         "an answered turn filed an empty-turn marker")

    def test_a_banner_that_never_reached_the_user_is_not_reported_as_shown(self):
        """`notice=` must be measured, not assumed.

        The banner write and the socket emit live inside a try/except here, so
        "we surfaced it" is an outcome that may not have occurred. The report
        counts a blank-with-an-explanation and a blank-with-nothing in different
        columns, and if this line lied the operator would read the second as the
        first -- the failure looking better than it was, which is the direction
        that never gets caught by anyone.
        """
        log, chats, events = _run([{"id": "c-1"}, {"id": "c-1"}],
                                  [dict(i) for i in THINKING_ONLY],
                                  emitter_raises=True)
        self.assertEqual(1, len(log.markers), log.markers)
        self.assertIn("notice=undelivered", log.markers[0],
                      "a banner the socket never delivered was filed as shown")
        # ...and the failure is still filed as an empty turn, with its trace.
        self.assertTrue(log.markers[0].startswith("empty_reply service=owui "),
                        log.markers[0])

    def test_the_trace_the_user_is_told_to_quote_is_the_trace_ops_can_find(self):
        """The defect this closes, stated as a test.

        On 2026-08-12 two users were shown "share trace t-... with ops" and the
        id existed in exactly one place: the error blob in their own chat row.
        Nothing logged it in a shape the journey ledger could read, the owui
        container was recreated three hours later, and the trace resolved to
        nothing. The banner and the marker have to carry the SAME id, or the
        instruction on screen is a lie.
        """
        log, chats, events = _run([{"id": "c-1"}, {"id": "c-1"}],
                                  [dict(i) for i in THINKING_ONLY])
        written = [p["error"] for p in chats.calls if "error" in p]
        self.assertEqual(1, len(written), "expected exactly one error write")
        banner_trace = written[0]["trace_id"]

        self.assertEqual(1, len(log.markers),
                         f"expected one empty-turn marker, got {log.markers}")
        marker = log.markers[0]
        self.assertIn(f"trace={banner_trace}", marker,
                      "the marker does not carry the id the user was shown")
        self.assertTrue(marker.startswith("empty_reply service=owui "),
                        f"marker is not in the ledger's grammar: {marker}")
        self.assertIn("phase=interrupted", marker,
                      "a cancelled turn was filed as a clean finalize")
        self.assertIn("notice=written", marker,
                      "the banner reached the user and the marker does not say so")
        # M4: ids and closed-vocabulary labels only. THINKING_ONLY carries the
        # model's reasoning text; none of it may reach a line ops greps.
        for item in THINKING_ONLY:
            for part in item.get("content", []) or []:
                text = (part.get("text") or "").strip()
                if text:
                    self.assertNotIn(text, marker,
                                     "reasoning text leaked into the marker")


if __name__ == "__main__":
    unittest.main()


def _lift_finalized_empty_block():
    """Compile the FINALIZED empty-turn block out of middleware.py.

    The interrupted path is a nested function and lifts whole; the finalized one
    lives inside `response_handler`, 1900 lines of it, so the unit here is the
    `try/finally` STATEMENT rather than a function. Same principle: real
    production source, synthetic collaborators, no stack.

    Round 28 proved why this needed to exist. Written straight through, a raise
    from either await jumped clean over the marker, so the worst version of the
    failure -- the one where we could not even tell the user -- was the one that
    left no record. Only the interrupted path had a behavioural suite; this
    branch had none at all.
    """
    src = MIDDLEWARE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for parent in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            siblings = getattr(parent, field, None)
            if not isinstance(siblings, list):
                continue
            for i, node in enumerate(siblings):
                if not isinstance(node, ast.Try) or not node.finalbody:
                    continue
                if "PHASE_FINALIZED" not in ast.dump(
                        ast.Module(body=node.finalbody, type_ignores=[])):
                    continue
                # Start from the statement BEFORE the try, because
                # `notice = NOTICE_UNDELIVERED` lives there and is
                # load-bearing: it is the value the marker reports when the
                # try body never reaches its own reassignment. Lifting from
                # `try:` alone made both failure fixtures die on NameError
                # while the happy path passed -- a lift that quietly tested
                # only the branch that cannot fail.
                start = siblings[i - 1] if i else node
                block = textwrap.dedent(
                    "".join(src.splitlines(True)[start.lineno - 1:node.end_lineno]))
                return "async def _probe():\n" + textwrap.indent(block, "    ")
    raise AssertionError("the finalized empty-turn block is no longer findable")


class FinalizedEmptyTurnTests(unittest.TestCase):
    """Three fixtures, because there are three outcomes and they must differ."""

    def _run_finalized(self, upsert_raises=False, emit_raises=False):
        markers = []
        handler = logging.Handler()
        handler.emit = lambda rec: markers.append(rec.getMessage())
        marker_log = logging.getLogger("open_webui.utils.failure_surface")
        marker_log.addHandler(handler)
        marker_log.setLevel(logging.INFO)

        class _Chats:
            calls = []

            async def upsert_message_to_chat_by_id_and_message_id(self, c, m, p):
                if upsert_raises:
                    raise RuntimeError("the database is gone")
                return {"id": c}

        async def event_emitter(evt):
            if emit_raises:
                raise RuntimeError("socket.io is down")

        ns = {
            "Chats": _Chats(), "event_emitter": event_emitter,
            "metadata": {"chat_id": "c-final", "message_id": "m-final"},
            "empty_error": failure_surface.build_error_payload("c-final", "m-final"),
            "log_empty_turn": failure_surface.log_empty_turn,
            "PHASE_FINALIZED": failure_surface.PHASE_FINALIZED,
            "NOTICE_WRITTEN": failure_surface.NOTICE_WRITTEN,
            "NOTICE_UNDELIVERED": failure_surface.NOTICE_UNDELIVERED,
        }
        raised = None
        try:
            exec(compile(_lift_finalized_empty_block(), "<lifted-final>", "exec"), ns)
            asyncio.run(ns["_probe"]())
        except Exception as exc:
            raised = exc
        finally:
            marker_log.removeHandler(handler)
        return markers, raised

    def test_both_succeeded_is_reported_as_written(self):
        markers, raised = self._run_finalized()
        self.assertIsNone(raised)
        self.assertEqual(1, len(markers), markers)
        self.assertIn("notice=written", markers[0])
        self.assertIn("phase=finalized", markers[0])

    def test_a_dead_socket_still_files_the_marker_and_says_undelivered(self):
        markers, raised = self._run_finalized(emit_raises=True)
        self.assertEqual(1, len(markers),
                         f"the emit failed and took the record with it: {markers}")
        self.assertIn("notice=undelivered", markers[0])

    def test_a_dead_database_still_files_the_marker(self):
        """The worst case: we could not even write the banner. Losing the marker
        here means the failure that is hardest to explain is also the one with
        no trace at all."""
        markers, raised = self._run_finalized(upsert_raises=True)
        self.assertEqual(1, len(markers),
                         f"a DB failure produced no marker: {markers}")
        self.assertIn("notice=undelivered", markers[0])
        # ...and the original exception is NOT swallowed by the observability.
        self.assertIsNotNone(raised, "the DB failure was silently absorbed")


def _lift_consume_catch():
    """The `try/except StreamReadFailure/except Exception` around the handler.

    Round 36 proved this branch mattered and no test touched it: reverting it to
    classify EVERY exception escaping the handler left the whole suite green,
    which is how a `TimeoutError` from a UI event emitter came to be reported as
    `upstream_timeout` from the model.
    """
    src = MIDDLEWARE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = [getattr(h.type, "id", "") for h in node.handlers]
        if "StreamReadFailure" in names:
            block = textwrap.dedent(
                "".join(src.splitlines(True)[node.lineno - 1:node.end_lineno]))
            return "async def _probe():\n" + textwrap.indent(block, "    ")
    raise AssertionError("the consume-stream catch is no longer findable")


class ReadBoundaryClassificationTests(unittest.TestCase):
    def _run(self, raiser):
        seen = {}

        async def save_interrupted_state(reason, failure=None):
            seen["reason"] = reason
            seen["failure"] = failure

        async def stream_body_handler(resp, fd):
            raise raiser

        ns = {
            "asyncio": asyncio,
            "stream_body_handler": stream_body_handler,
            "save_interrupted_state": save_interrupted_state,
            "StreamReadFailure": failure_surface.StreamReadFailure,
            "StreamBodyInterrupted": type("StreamBodyInterrupted", (Exception,), {}),
            "FAILURE_KIND_UNCLASSIFIED": failure_surface.FAILURE_KIND_UNCLASSIFIED,
            "resp": object(), "fd": object(),
        }
        exec(compile(_lift_consume_catch(), "<lifted-catch>", "exec"), ns)
        with self.assertRaises(ns["StreamBodyInterrupted"]):
            asyncio.run(ns["_probe"]())
        return seen

    def test_a_classified_READ_failure_keeps_its_label(self):
        klass = type("TransferEncodingError", (Exception,), {})
        klass.__module__ = "aiohttp.http_exceptions"
        seen = self._run(failure_surface.StreamReadFailure("stream_truncated", klass()))
        # `upstream_read_failed`, not the shared `stream_failed`: the reason now
        # says WHERE, so the report can stop calling an internal failure a
        # transport one.
        self.assertEqual("upstream_read_failed", seen["reason"])
        self.assertEqual("stream_truncated", seen["failure"])

    def test_a_failure_from_ANYWHERE_ELSE_is_unclassified(self):
        """A TimeoutError from a UI emitter is not an upstream timeout.

        Same class, different fact. This is the one that would send an operator
        to the provider for a failure in our own notification path.
        """
        seen = self._run(TimeoutError("event_emitter flush timed out"))
        # A DIFFERENT reason from the read failure above, which is the whole
        # point: same exception class, different place, different operator.
        self.assertEqual("stream_processing_failed", seen["reason"])
        self.assertEqual("unclassified", seen["failure"])


class BodyReadsGoThroughTheAdapterTests(unittest.TestCase):
    """A SMOKE ALARM, and labelled as one.

    Bypassing `classified_body_reads` loses every transport classification
    silently — the field stays present and reads `unclassified` forever, which
    looks exactly like a quiet week. The behavioural tests above cannot see it:
    they supply their own `stream_body_handler`, and the real iteration lives
    inside a 600-line function that does not lift.

    So this reads the source, which is weaker than executing it, and says so.
    The durable version is the typed read adapter being the ONLY way to obtain
    the body — not achievable without restructuring that function.
    """

    def test_the_response_body_is_iterated_through_the_adapter(self):
        src = MIDDLEWARE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Not every body_iterator in this file is the user's stream.
        # `chat_completion_tools_handler.get_content_from_response` drains a
        # NON-streaming tool response whose failures are not transport failures
        # of anyone's answer. Naming it is the difference between an allow-list
        # and a hole: if a second raw site appears, this goes red.
        _ALLOWED_RAW = {"get_content_from_response"}
        # INNERMOST enclosing function. Walking each function and recursing
        # reported the OUTER `chat_completion_tools_handler` for a loop that
        # actually lives in its nested `get_content_from_response`, so the
        # allow-list missed and this failed on correct code. A containment test
        # that names the wrong container is not a stricter test.
        raw, adapted = [], []
        _owner = {}

        def _mark(node, name):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _mark(child, child.name)
                else:
                    _owner[id(child)] = name
                    _mark(child, name)

        _mark(tree, "<module>")
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFor):
                continue
            it = node.iter
            owner = _owner.get(id(node), "<module>")
            if isinstance(it, ast.Attribute) and it.attr == "body_iterator":
                if owner not in _ALLOWED_RAW:
                    raw.append(f"{owner}:{node.lineno}")
            if (isinstance(it, ast.Call)
                    and getattr(it.func, "id", "") == "_classified_body_reads"):
                adapted.append(node.lineno)
        self.assertTrue(adapted,
                        "no async-for iterates the body through "
                        "_classified_body_reads; transport failures can no "
                        "longer be classified at the read")
        self.assertEqual([], raw,
                         f"the response body is iterated RAW at line(s) {raw}; "
                         f"failures there reach the outer catch unclassified")
