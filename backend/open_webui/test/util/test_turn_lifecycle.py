"""Accepted-execution conservation, schema 1 — the producer half.

The one property this slice sells is arithmetic: every accepted attempt emits
exactly one start and at most one terminal. If exactly-once fails, the
denominator is wrong in a way no downstream reader can detect — a double
terminal looks exactly like two healthy attempts.

The other half of the risk is meaning. `returned` must never be readable as
"the user got an answer". That is not a property a unit test can assert about a
person, so what is asserted here is the next best thing: the record physically
cannot carry a user-outcome field, because nothing user-facing is accepted into
the grammar at all.
"""
import asyncio
import importlib.util
import logging
import os
import threading
import unittest

_HERE = os.path.dirname(__file__)
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "utils",
                                     "turn_lifecycle.py"))


def _load():
    """Load by path: importing the package pulls the whole app in."""
    spec = importlib.util.spec_from_file_location("_tl_under_test", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tl = _load()


class ExactlyOnceTests(unittest.TestCase):
    def test_a_second_finish_emits_nothing_and_says_so(self):
        a = tl.Attempt("a" * 32, tl.SCOPE_API, tl.MODE_TASK)
        self.assertTrue(a.finish(tl.OUTCOME_RETURNED))
        self.assertFalse(a.finish(tl.OUTCOME_RAISED))
        self.assertFalse(a.finish(tl.OUTCOME_CANCELLED))

    def test_concurrent_owners_produce_exactly_one_winner(self):
        """Several owners racing to finish is the DESIGN, not a bug.

        A done-callback, an except path and a shutdown takeover may all try. The
        return value is how the losers learn to stay silent instead of guessing.
        `add_done_callback` can also fire from another thread when the loop is
        driven by `run_coroutine_threadsafe`, so this is a real race and not a
        theoretical one.
        """
        a = tl.Attempt("b" * 32, tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
        wins, barrier = [], threading.Barrier(8)

        def race():
            barrier.wait()
            wins.append(a.finish(tl.OUTCOME_RETURNED))

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for w in wins if w), 1)
        self.assertEqual(len(wins), 8)

    def test_an_invalid_outcome_writes_nothing_rather_than_a_healthy_label(self):
        """The attempt is still consumed.

        A programming slip must not break the turn, and it must not become
        `returned` either — that would move an unobserved attempt into the
        healthy count, which is the exact move that made three earlier labels
        lie. Consuming it leaves a start with no terminal, which the reader
        already renders as unmeasured.
        """
        a = tl.Attempt("c" * 32, tl.SCOPE_LOCAL, tl.MODE_INLINE)
        self.assertTrue(a.finish("delivered"))
        self.assertTrue(a.finished)
        self.assertFalse(a.finish(tl.OUTCOME_RETURNED))


class GrammarTests(unittest.TestCase):
    def test_the_five_outcomes_are_the_whole_vocabulary(self):
        self.assertEqual(
            tl.ALLOWED_OUTCOMES,
            {"returned", "raised", "cancelled", "not_started", "unknown"})

    def test_no_user_outcome_can_enter_the_grammar(self):
        """The forbidden day-one claims, refused structurally.

        Not a naming convention: the builder validates against a closed set, so
        a well-meaning caller cannot introduce `answered` or `delivered` without
        editing the vocabulary and meeting this test.
        """
        for forbidden in ("answered", "blank", "complete", "saved", "delivered",
                          "success", "failed"):
            with self.assertRaises(ValueError):
                tl.build_turn_terminal("d" * 32, tl.SCOPE_API, tl.MODE_TASK, forbidden)

    def test_the_attempt_id_binds_to_nothing(self):
        """Opaque by construction. 32 lower hex and no join key anywhere.

        An id joinable to a chat or a person would invite exactly the inferences
        schema 1 is too small to support.
        """
        line = tl.build_turn_started(tl.new_attempt_id(), tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
        for leak in ("chat=", "msg=", "uid=", "model=", "user=", "trace"):
            self.assertNotIn(leak, line)

    def test_a_malformed_id_is_refused_rather_than_emitted(self):
        for bad in ("", "ABCDEF" + "0" * 26, "a" * 31, "a" * 33, "g" * 32):
            with self.assertRaises(ValueError):
                tl.build_turn_started(bad, tl.SCOPE_API, tl.MODE_TASK)

    def test_start_returns_none_for_an_invalid_scope(self):
        """Never raise into a turn for an observability failure."""
        self.assertIsNone(tl.start("nonsense", tl.MODE_TASK))
        self.assertIsNone(tl.start(tl.SCOPE_API, "sideways"))

    def test_capability_is_capability_not_evidence(self):
        """It says this build CAN emit, never that anything was emitted.

        The reader needs it to tell a real zero from a build that predates the
        producer — the difference between "nothing failed" and "nothing looked".
        """
        self.assertEqual(tl.capability(), {"owui_turn_lifecycle": tl.SCHEMA})


class SingleChannelTests(unittest.TestCase):
    def test_exactly_one_logging_call_in_the_module(self):
        """What makes "any line on this logger came from here" true.

        The collector allow-lists this logger wholesale instead of matching
        prose, so a second emitter beside `_emit` would silently widen what the
        ledger trusts. Same guard `failure_surface` carries.
        """
        import ast
        tree = ast.parse(open(_SRC, encoding="utf-8").read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "_LIFECYCLE_LOG"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].func.attr, "info")


if __name__ == "__main__":
    unittest.main()


class ClassifyFinalTaskStateTests(unittest.IsolatedAsyncioTestCase):
    """The disposition must come from the FINAL Task state, never race order.

    Round 75 drove each of these against real asyncio and they are the reason
    the terminal belongs to the done callback alone: a coroutine-level `finally`
    can emit `returned` and then be overturned by cleanup that raises.
    """

    async def _finished(self, coro):
        task = asyncio.ensure_future(coro)
        try:
            await task
        except BaseException:  # noqa: BLE001 -- disposition is the subject
            pass
        return task

    async def test_a_suppressed_cancellation_is_returned_not_cancelled(self):
        """`cancel()` is INTENT, not disposition.

        Latching "cancel was requested" as the outcome would report a turn the
        user completed normally as one they stopped.
        """
        async def suppresses():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return "recovered"

        task = asyncio.ensure_future(suppresses())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertFalse(task.cancelled())
        self.assertEqual(tl.classify_task(task), tl.OUTCOME_RETURNED)

    async def test_a_cancellation_overridden_by_cleanup_is_raised(self):
        async def cleanup_raises():
            try:
                await asyncio.sleep(10)
            finally:
                raise RuntimeError("cleanup")

        task = asyncio.ensure_future(cleanup_raises())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001
            pass
        self.assertFalse(task.cancelled())
        self.assertEqual(tl.classify_task(task), tl.OUTCOME_RAISED)

    async def test_cancellation_before_first_entry_is_cancelled(self):
        """The coroutine body never runs, and an inner `finally` never could."""
        entered = []

        async def never_entered():
            entered.append(True)

        task = asyncio.ensure_future(never_entered())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertEqual(entered, [])
        self.assertEqual(tl.classify_task(task), tl.OUTCOME_CANCELLED)

    async def test_a_non_cancelled_base_exception_is_raised(self):
        """Not narrowed to `Exception`, deliberately.

        A bespoke BaseException rather than KeyboardInterrupt: the real one
        escapes the test runner itself, which proves the point but ends the
        suite.
        """
        class NotAnException(BaseException):
            pass

        async def base_raiser():
            raise NotAnException("not an Exception subclass")

        task = await self._finished(base_raiser())
        self.assertEqual(tl.classify_task(task), tl.OUTCOME_RAISED)

    async def test_an_ordinary_return_is_returned(self):
        async def fine():
            return "answer"

        task = await self._finished(fine())
        self.assertEqual(tl.classify_task(task), tl.OUTCOME_RETURNED)

    async def test_an_uninspectable_owner_is_unknown_not_raised(self):
        """An instrumentation failure must not invent a provider-shaped story."""
        class Uninspectable:
            def cancelled(self):
                raise RuntimeError("cannot inspect")

            def exception(self):
                raise RuntimeError("cannot inspect")

        self.assertEqual(tl.classify_task(Uninspectable()), tl.OUTCOME_UNKNOWN)


class RegistryOrderingTests(unittest.TestCase):
    def test_the_lifecycle_is_registered_before_the_start_line(self):
        """Why orphan terminals stay exceptional rather than structural.

        If the owner were constructed before registration, a fast owner's done
        callback could terminalize against a lifecycle that does not exist yet —
        turning an instrumentation fault into ordinary traffic.
        """
        before = tl.live_attempts()
        a = tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
        self.assertTrue(a.start_attempted)
        self.assertEqual(tl.live_attempts(), before + 1)
        a.finish(tl.OUTCOME_RETURNED)
        self.assertEqual(tl.live_attempts(), before)

    def test_finish_removes_only_its_own_lifecycle(self):
        a = tl.start(tl.SCOPE_API, tl.MODE_INLINE)
        impostor = tl.Attempt(a.id, tl.SCOPE_API, tl.MODE_INLINE)
        impostor.finish(tl.OUTCOME_RETURNED)
        self.assertIn(a.id, tl._REGISTRY)
        a.finish(tl.OUTCOME_RETURNED)
        self.assertNotIn(a.id, tl._REGISTRY)


class StartIsANonInterferenceBoundaryTests(unittest.TestCase):
    """A producer of observability must never break the turn it observes.

    Round 76 P0: `new_attempt_id()` sat OUTSIDE the guard, so an ordinary
    entropy failure escaped `start()` into the request path. The probe was an
    `OSError` from the RNG — rare, but the failure mode is "a user's turn dies
    because we wanted to count it", which is never an acceptable trade.
    """

    def test_an_entropy_failure_returns_none_instead_of_raising(self):
        real = tl.secrets.token_hex

        def boom(_n):
            raise OSError("synthetic entropy failure")

        tl.secrets.token_hex = boom
        try:
            self.assertIsNone(tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK))
        finally:
            tl.secrets.token_hex = real

    def test_a_failed_start_leaves_no_live_attempt_behind(self):
        """A leaked registry entry is a start that can never be finished."""
        real = tl.secrets.token_hex
        before = tl.live_attempts()

        def boom(_n):
            raise OSError("synthetic entropy failure")

        tl.secrets.token_hex = boom
        try:
            tl.start(tl.SCOPE_API, tl.MODE_TASK)
        finally:
            tl.secrets.token_hex = real
        self.assertEqual(tl.live_attempts(), before)

    def test_process_control_still_propagates_and_cleans_up(self):
        """Swallowing SystemExit would be its own hazard.

        This module has no business deciding a shutdown does not apply to it —
        but a killed start must not leave a live attempt either.
        """
        real = tl.secrets.token_hex
        before = tl.live_attempts()

        def boom(_n):
            raise SystemExit("shutting down")

        tl.secrets.token_hex = boom
        try:
            with self.assertRaises(SystemExit):
                tl.start(tl.SCOPE_API, tl.MODE_TASK)
        finally:
            tl.secrets.token_hex = real
        self.assertEqual(tl.live_attempts(), before)

    def test_a_raising_log_sink_does_not_break_the_turn(self):
        """The sink is the other way observability reaches into the request."""
        log = logging.getLogger("open_webui.turn_lifecycle")

        class Hostile(logging.Handler):
            def emit(self, record):
                raise RuntimeError("synthetic sink failure")

        handler = Hostile()
        log.addHandler(handler)
        before = tl.live_attempts()
        try:
            attempt = tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
            self.assertIsNotNone(attempt)
            self.assertTrue(attempt.finish(tl.OUTCOME_RETURNED))
        finally:
            log.removeHandler(handler)
        self.assertEqual(tl.live_attempts(), before)
