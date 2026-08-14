"""The REAL `create_task`, driven — not a local reimplementation of its shape.

Round 73 caught me guarding a shipped invariant with a test that rebuilt the
production shape inside itself: it proved the shape I had written down, not the
shape that runs. So this loads the actual `tasks.py` and calls the actual
`create_task`, with a fake redis and real asyncio Tasks.

What must hold, in one sentence: every accepted execution emits exactly one
terminal, and that terminal says what the Task's FINAL state said.
"""
import asyncio
import importlib.util
import io
import logging
import os
import sys
import types
import unittest

_HERE = os.path.dirname(__file__)
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_TASKS = os.path.normpath(os.path.join(_HERE, "..", "..", "tasks.py"))
_LIFECYCLE = os.path.normpath(os.path.join(_HERE, "..", "..", "utils",
                                           "turn_lifecycle.py"))


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tl = _load(_LIFECYCLE, "_tl_wiring")


def _load_tasks():
    """`tasks.py` without dragging the app in.

    It needs `open_webui.env.REDIS_KEY_PREFIX` and `open_webui.utils
    .turn_lifecycle`; both are stubbed to the real lifecycle module so the
    subject under test is the real one.
    """
    pkg = types.ModuleType("open_webui")
    pkg.__path__ = []
    env = types.ModuleType("open_webui.env")
    env.REDIS_KEY_PREFIX = "test"
    utils = types.ModuleType("open_webui.utils")
    utils.__path__ = []
    utils.turn_lifecycle = tl
    for name, mod in (("open_webui", pkg), ("open_webui.env", env),
                      ("open_webui.utils", utils),
                      ("open_webui.utils.turn_lifecycle", tl)):
        sys.modules.setdefault(name, mod)
    return _load(_TASKS, "_tasks_wiring")


tasks_mod = _load_tasks()


class _Capture:
    """Every line the producer actually logged, in order."""

    def __enter__(self):
        self.buf = io.StringIO()
        self.handler = logging.StreamHandler(self.buf)
        self.log = logging.getLogger("open_webui.turn_lifecycle")
        self.log.addHandler(self.handler)
        self.log.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        self.log.removeHandler(self.handler)

    def terminals(self):
        return [ln for ln in self.buf.getvalue().splitlines()
                if ln.startswith("turn_terminal")]

    def starts(self):
        return [ln for ln in self.buf.getvalue().splitlines()
                if ln.startswith("turn_started")]

    def outcome(self):
        terminals = self.terminals()
        assert len(terminals) == 1, f"expected one terminal, got {terminals}"
        return terminals[0].split("execution_outcome=")[1].split()[0]


class CreateTaskOwnsTheTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, coro, cancel=False, let_it_start=False):
        """`let_it_start` is the whole difference between two real cases.

        Cancelling before the coroutine has ever been entered is `cancelled` --
        the body never runs. Cancelling after it is parked on an await, where
        it can catch CancelledError and return, is `returned`. The first version
        of the suppression test skipped the yield and therefore measured
        pre-entry cancellation while claiming to measure suppression.
        """
        with _Capture() as cap:
            lc = tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
            _task_id, task = await tasks_mod.create_task(
                None, coro, id="chat-1", lifecycle=lc)
            if let_it_start:
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            if cancel:
                task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001 -- disposition is the subject
                pass
            # done callbacks are scheduled with call_soon
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return cap

    async def test_a_normal_return_emits_exactly_one_returned(self):
        async def fine():
            return "answer"

        cap = await self._run(fine())
        self.assertEqual(len(cap.starts()), 1)
        self.assertEqual(cap.outcome(), "returned")

    async def test_a_raise_emits_exactly_one_raised(self):
        async def boom():
            raise RuntimeError("provider blew up")

        cap = await self._run(boom())
        self.assertEqual(cap.outcome(), "raised")

    async def test_a_cancellation_emits_exactly_one_cancelled(self):
        """Cancelled after entry, with nothing catching it."""
        async def slow():
            await asyncio.sleep(30)

        cap = await self._run(slow(), cancel=True, let_it_start=True)
        self.assertEqual(cap.outcome(), "cancelled")

    async def test_a_cancellation_before_first_entry_is_also_cancelled(self):
        """The body never runs, so no coroutine-level hook could ever fire."""
        entered = []

        async def never_entered():
            entered.append(True)
            await asyncio.sleep(30)

        cap = await self._run(never_entered(), cancel=True)
        self.assertEqual(entered, [])
        self.assertEqual(cap.outcome(), "cancelled")

    async def test_a_suppressed_cancellation_is_returned_through_the_real_path(self):
        """The trap the done-callback design exists for.

        A coroutine-level terminal would have said `cancelled` here — the
        cancel was requested — and been wrong, because the coroutine swallowed
        it and returned normally.
        """
        async def suppresses():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return "recovered"

        cap = await self._run(suppresses(), cancel=True, let_it_start=True)
        self.assertEqual(cap.outcome(), "returned")

    async def test_cleanup_that_raises_overturns_the_return(self):
        """...and the other half: a chosen return replaced by cleanup."""
        async def cleanup_raises():
            try:
                return "answer"
            finally:
                raise RuntimeError("cleanup blew up")

        cap = await self._run(cleanup_raises())
        self.assertEqual(cap.outcome(), "raised")

    async def test_a_failed_task_construction_emits_not_started_and_closes_it(self):
        """`not_started` means NO OWNER EXISTED — the only case that qualifies.

        The coroutine is closed so this rare path does not also produce a
        "coroutine was never awaited" warning, and the original failure must
        still reach the caller unmasked.
        """
        entered = []

        async def never_runs():
            entered.append(True)

        coro = never_runs()
        real = asyncio.create_task

        def boom(*a, **k):
            raise RuntimeError("loop refused the task")

        with _Capture() as cap:
            lc = tl.start(tl.SCOPE_API, tl.MODE_TASK)
            asyncio.create_task = boom
            try:
                with self.assertRaises(RuntimeError):
                    await tasks_mod.create_task(None, coro, id="c",
                                                lifecycle=lc)
            finally:
                asyncio.create_task = real

        self.assertEqual(entered, [], "the coroutine body must never run")
        self.assertEqual(cap.outcome(), "not_started")

    async def test_a_redis_failure_never_becomes_not_started(self):
        """A Task already exists by then. `not_started` would be a lie.

        Round 75 trap 5 — and the reason the emitter is not allowed to treat
        "bookkeeping failed" as "nothing ran".
        """
        async def fine():
            return "answer"

        class BoomRedis:
            def pipeline(self):
                raise RuntimeError("redis down")

        with _Capture() as cap:
            lc = tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
            with self.assertRaises(RuntimeError):
                await tasks_mod.create_task(BoomRedis(), fine(), id="c",
                                            lifecycle=lc)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(cap.outcome(), "returned")

    async def test_a_callback_registration_failure_is_unknown_not_not_started(self):
        """The owner exists; only our ability to watch it was lost."""
        async def fine():
            return "answer"

        coro = fine()
        real_create = asyncio.create_task

        class Blind:
            """A real Task that refuses exactly one callback registration."""

            def __init__(self, task):
                self._task = task
                self._refused = False

            def add_done_callback(self, cb):
                if not self._refused:
                    self._refused = True
                    raise RuntimeError("cannot register")
                return self._task.add_done_callback(cb)

            def __getattr__(self, name):
                return getattr(self._task, name)

        with _Capture() as cap:
            lc = tl.start(tl.SCOPE_STORED_CHAT, tl.MODE_TASK)
            wrapped = {}

            def make(c, **k):
                t = real_create(c, **k)
                wrapped["t"] = t
                return Blind(t)

            asyncio.create_task = make
            try:
                await tasks_mod.create_task(None, coro, id="c", lifecycle=lc)
            finally:
                asyncio.create_task = real_create
            await wrapped["t"]
            await asyncio.sleep(0)
            self.assertEqual(cap.outcome(), "unknown")

    async def test_no_lifecycle_leaves_create_task_completely_unchanged(self):
        """The parameter is optional and every existing caller passes nothing."""
        async def fine():
            return "answer"

        with _Capture() as cap:
            task_id, task = await tasks_mod.create_task(None, fine(), id="c")
            self.assertEqual(await task, "answer")
            await asyncio.sleep(0)
            self.assertEqual(cap.starts(), [])
            self.assertEqual(cap.terminals(), [])
        self.assertIsInstance(task_id, str)


if __name__ == "__main__":
    unittest.main()
