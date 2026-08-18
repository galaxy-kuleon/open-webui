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
from unittest import mock

_HERE = os.path.dirname(__file__)
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
_TASKS = os.path.normpath(os.path.join(_HERE, "..", "..", "tasks.py"))
_LIFECYCLE = os.path.normpath(os.path.join(_HERE, "..", "..", "utils",
                                           "turn_lifecycle.py"))


_ABSENT = object()


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


tl = _load(_LIFECYCLE, "_tl_wiring")


def _load_tasks():
    """`tasks.py` without dragging the app in — and WITHOUT poisoning the run.

    It needs `open_webui.env.REDIS_KEY_PREFIX` and
    `open_webui.utils.turn_lifecycle`. The first version installed those stubs
    permanently, so every OTHER test in the same pytest session then imported a
    fake `open_webui` package with an empty `__path__` and died at collection.
    A test that breaks its neighbours is worse than one that fails.

    So the stubs live only for the duration of the load, and whatever was there
    before is put back exactly — including "was not present at all".
    """
    saved = {name: sys.modules.get(name, _ABSENT)
             for name in ("open_webui", "open_webui.env", "open_webui.utils",
                          "open_webui.utils.turn_lifecycle")}
    pkg = types.ModuleType("open_webui")
    pkg.__path__ = []
    env = types.ModuleType("open_webui.env")
    env.REDIS_KEY_PREFIX = "test"
    utils = types.ModuleType("open_webui.utils")
    utils.__path__ = []
    utils.turn_lifecycle = tl
    try:
        sys.modules["open_webui"] = pkg
        sys.modules["open_webui.env"] = env
        sys.modules["open_webui.utils"] = utils
        sys.modules["open_webui.utils.turn_lifecycle"] = tl
        return _load(_TASKS, "_tasks_wiring")
    finally:
        for name, previous in saved.items():
            if previous is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


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
        self.assertTrue(
            hasattr(tl, "owner_observation"),
            "the production lifecycle has no owner-observation boundary",
        )
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
            observation = tl.owner_observation(1)
            self.assertEqual(observation["status"], "unknown")
            self.assertIsNone(observation["active"])
            self.assertEqual(observation["reason"],
                             "callback_registration_failed")

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


class PersistedPlaceholderLicenceTests(unittest.TestCase):
    def test_main_forwards_the_repository_returned_assistant_row(self):
        """Drive the real forwarding helper; request ids are not its oracle."""
        import open_webui.main as main

        licenses = tl.TurnBindingLicenses()
        persisted = types.SimpleNamespace(
            id="chat-persisted",
            chat={"history": {"messages": {
                "parent-row": {"id": "parent-row", "role": "user"},
                "assistant-row": {"id": "assistant-row",
                                  "parentId": "parent-row",
                                  "role": "assistant"},
            }}},
        )
        main._license_persisted_placeholder(
            licenses, persisted, "assistant-row")
        self.assertEqual(
            licenses.take("assistant-row"),
            tl.make_turn_ref("chat-persisted", "assistant-row"),
        )
        self.assertIsNone(licenses.take("parent-row"))


class OwnerObservationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_admin_route_preserves_zero_and_unknown_on_the_wire(self):
        import open_webui.main as main

        self.assertTrue(
            hasattr(main, "owner_observation_endpoint"),
            "the production app has no owner-observation endpoint",
        )

        payloads = (
            {"schema": 1, "status": "observed", "active": 0, "reason": None,
             "scope": "accepted_executions", "worker_count": 1},
            {"schema": 1, "status": "unknown", "active": None,
             "reason": "start_unobserved", "scope": "accepted_executions",
             "worker_count": 1},
        )
        for payload in payloads:
            with self.subTest(status=payload["status"]), mock.patch.object(
                    main.turn_lifecycle, "owner_observation",
                    return_value=payload) as observe:
                self.assertEqual(
                    await main.owner_observation_endpoint(user=object()), payload)
                observe.assert_called_once_with(main.UVICORN_WORKERS)

    async def test_the_count_is_admin_authenticated_not_public_health_data(self):
        import open_webui.main as main

        self.assertTrue(
            hasattr(main, "owner_observation_endpoint"),
            "the production app has no owner-observation endpoint",
        )

        route = next(
            r for r in main.app.routes
            if getattr(r, "path", None) == "/api/tasks/owner-observation")
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies}
        self.assertIn(main.get_admin_user, dependency_calls)
        health = next(
            r for r in main.app.routes if getattr(r, "path", None) == "/health")
        self.assertNotEqual(route.endpoint, health.endpoint)
        health_payload = await main.healthcheck()
        self.assertTrue(
            {"active", "reason", "scope", "worker_count"}.isdisjoint(
                health_payload
            ),
            "the operational owner count leaked onto unauthenticated /health",
        )


class RealChatCompletionBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_chat_fanout_binds_each_confirmed_assistant_once(self):
        """The insert return licenses both persisted rows, not one request."""
        import open_webui.main as main

        request = types.SimpleNamespace(
            state=types.SimpleNamespace(),
            app=types.SimpleNamespace(state=types.SimpleNamespace(
                MODELS={"m1": {"id": "m1"}, "m2": {"id": "m2"}},
                redis=None,
                config=types.SimpleNamespace(DEFAULT_MODEL_PARAMS={}),
            )),
        )
        user = types.SimpleNamespace(id="user", role="admin")
        form = {
            "model": "m1", "model_item": {"direct": True},
            "parent_id": None, "session_id": "session",
            "message_ids": {"m1": "assistant-one", "m2": "assistant-two"},
        }
        inserted = types.SimpleNamespace(
            id="chat-new",
            chat={"history": {"messages": {
                "assistant-one": {"id": "assistant-one", "role": "assistant"},
                "assistant-two": {"id": "assistant-two", "role": "assistant"},
            }}},
        )
        task_number = 0

        async def own_task(_redis, coroutine, *, id=None, lifecycle=None):
            nonlocal task_number
            coroutine.close()
            self.assertIsNotNone(lifecycle)
            lifecycle.finish(tl.OUTCOME_RETURNED)
            task_number += 1
            return f"task-{task_number}", None

        with _Capture() as cap, \
                mock.patch.object(main, "uuid4", return_value="chat-new"), \
                mock.patch.object(main.Chats, "insert_new_chat",
                                  mock.AsyncMock(return_value=inserted)), \
                mock.patch.object(main, "create_task", side_effect=own_task), \
                mock.patch.object(main, "get_event_emitter",
                                  mock.AsyncMock(return_value=None)), \
                mock.patch.object(main.failure_surface, "log_turn_opened"):
            result = await main.chat_completion(request, form, user)

        self.assertEqual(result["task_ids"], ["task-1", "task-2"])
        bound = [line for line in cap.buf.getvalue().splitlines()
                 if line.startswith("turn_bound ")]
        self.assertEqual(len(bound), 2)
        self.assertEqual(
            {line.split("turn_ref=", 1)[1] for line in bound},
            {tl.make_turn_ref("chat-new", "assistant-one"),
             tl.make_turn_ref("chat-new", "assistant-two")},
        )

    async def test_existing_chat_forwards_the_confirmed_assistant_to_start(self):
        """The real caller, with I/O owners replaced but its ordering intact."""
        import open_webui.main as main

        request = types.SimpleNamespace(
            state=types.SimpleNamespace(),
            app=types.SimpleNamespace(state=types.SimpleNamespace(
                MODELS={"m": {"id": "m"}}, redis=None,
                config=types.SimpleNamespace(DEFAULT_MODEL_PARAMS={}),
            )),
        )
        user = types.SimpleNamespace(id="user", role="admin")
        form = {
            "model": "m", "model_item": {"direct": True},
            "chat_id": "chat-persisted", "session_id": "session",
            "message_ids": {"m": "assistant-row"},
        }

        async def upsert(chat_id, message_id, message):
            stored = {**message, "id": message_id}
            return types.SimpleNamespace(
                id=chat_id,
                chat={"history": {"messages": {message_id: stored}}},
            )

        async def own_task(_redis, coroutine, *, id=None, lifecycle=None):
            coroutine.close()
            self.assertIsNotNone(lifecycle)
            lifecycle.finish(tl.OUTCOME_RETURNED)
            return "task-id", None

        with _Capture() as cap, \
                mock.patch.object(main.Chats, "is_chat_owner",
                                  mock.AsyncMock(return_value=True)), \
                mock.patch.object(
                    main.Chats, "upsert_message_to_chat_by_id_and_message_id",
                    side_effect=upsert), \
                mock.patch.object(main, "create_task", side_effect=own_task), \
                mock.patch.object(main, "get_event_emitter",
                                  mock.AsyncMock(return_value=None)), \
                mock.patch.object(main.failure_surface, "log_turn_opened"):
            result = await main.chat_completion(request, form, user)

        self.assertEqual(result["task_ids"], ["task-id"])
        bound = [line for line in cap.buf.getvalue().splitlines()
                 if line.startswith("turn_bound ")]
        self.assertEqual(len(bound), 1)
        self.assertIn(
            "turn_ref=" + tl.make_turn_ref("chat-persisted", "assistant-row"),
            bound[0],
        )

    async def test_inline_caller_binds_the_same_confirmed_assistant(self):
        """No Task exists on this branch; the binding must not disappear."""
        import open_webui.main as main

        request = types.SimpleNamespace(
            state=types.SimpleNamespace(),
            app=types.SimpleNamespace(state=types.SimpleNamespace(
                MODELS={"m": {"id": "m"}}, redis=None,
                config=types.SimpleNamespace(DEFAULT_MODEL_PARAMS={}),
            )),
        )
        user = types.SimpleNamespace(id="user", role="admin")
        form = {
            "model": "m", "model_item": {"direct": True},
            "chat_id": "chat-persisted",
            "message_ids": {"m": "assistant-row"},
        }

        async def upsert(chat_id, message_id, message):
            return types.SimpleNamespace(
                id=chat_id,
                chat={"history": {"messages": {
                    message_id: {**message, "id": message_id},
                }}},
            )

        async def payload(_request, data, _user, metadata, _model):
            return data, metadata, []

        with _Capture() as cap, \
                mock.patch.object(main.Chats, "is_chat_owner",
                                  mock.AsyncMock(return_value=True)), \
                mock.patch.object(
                    main.Chats, "upsert_message_to_chat_by_id_and_message_id",
                    side_effect=upsert), \
                mock.patch.object(main.failure_surface, "log_turn_opened"), \
                mock.patch.object(main, "process_chat_payload",
                                  side_effect=payload), \
                mock.patch.object(main, "chat_completion_handler",
                                  mock.AsyncMock(return_value={"wire": "ok"})), \
                mock.patch.object(main, "build_chat_response_context",
                                  mock.AsyncMock(return_value={})), \
                mock.patch.object(main, "process_chat_response",
                                  mock.AsyncMock(return_value="answer")):
            result = await main.chat_completion(request, form, user)

        self.assertEqual(result, "answer")
        bound = [line for line in cap.buf.getvalue().splitlines()
                 if line.startswith("turn_bound ")]
        self.assertEqual(len(bound), 1)
        self.assertIn(
            "turn_ref=" + tl.make_turn_ref("chat-persisted", "assistant-row"),
            bound[0],
        )


if __name__ == "__main__":
    unittest.main()


class NothingRaisesBetweenStartAndOwnerTests(unittest.TestCase):
    """The window that produced a start with no owner and no terminal.

    `start()` was placed "after every per-model value that can raise" — except
    the background-task filter, which was still inline in the `process_chat`
    argument list and therefore evaluated AFTER the start. A non-mapping
    `background_tasks` from the request raised in that gap, and the reader would
    have called the result `terminal record missing`: indistinguishable from
    process death or log loss.

    Structural, and deliberately so. Driving `main.chat_completion` end to end
    would make this a stack test; what regressed is the ORDER of two statements,
    and that is exactly what this reads.
    """

    _MAIN = os.path.normpath(os.path.join(_HERE, "..", "..", "main.py"))

    def _fanout_block(self):
        import ast
        tree = ast.parse(open(self._MAIN, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            body = ast.unparse(node)
            if "turn_lifecycle.start" in body and "create_task" in body:
                return node
        self.fail("could not locate the fan-out loop")

    def test_start_is_the_last_statement_before_the_owner(self):
        import ast
        block = self._fanout_block()
        statements = [ast.unparse(s) for s in block.body]
        start_at = next(i for i, s in enumerate(statements)
                        if "turn_lifecycle.start" in s)
        owner_at = next(i for i, s in enumerate(statements)
                        if "create_task" in s)
        self.assertEqual(
            owner_at, start_at + 1,
            "a statement sits between the start and the owner; if it raises, "
            "the attempt has a start, no owner and no terminal")

    def test_the_background_task_filter_is_evaluated_before_the_start(self):
        import ast
        block = self._fanout_block()
        statements = [ast.unparse(s) for s in block.body]
        start_at = next(i for i, s in enumerate(statements)
                        if "turn_lifecycle.start" in s)
        filtered_at = next(i for i, s in enumerate(statements)
                           if s.startswith("execution_tasks ="))
        self.assertLess(filtered_at, start_at)

    def test_the_owner_call_carries_no_raising_expression(self):
        """The argument list must be names, not comprehensions.

        An expression inside the `create_task(...)` call is evaluated after the
        start line even though it reads as if it were part of the call.
        """
        import ast
        block = self._fanout_block()
        owner = next(s for s in block.body if "create_task" in ast.unparse(s))
        for node in ast.walk(owner):
            self.assertNotIsInstance(
                node, (ast.DictComp, ast.ListComp, ast.SetComp),
                "a comprehension in the owner's arguments is evaluated after "
                "the start line and can raise in the unguarded window")
