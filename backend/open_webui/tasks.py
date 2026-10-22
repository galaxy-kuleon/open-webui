# tasks.py
import asyncio
import inspect
import json
import logging
from contextlib import suppress
from itertools import groupby
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from open_webui.utils import turn_lifecycle
from open_webui.env import REDIS_KEY_PREFIX, REDIS_RESPONSE_STREAM_TTL, REDIS_TASK_TTL
from open_webui.utils.json_codec import JSONCodec, dumps_bytes
from open_webui.utils.misc import get_output_text
from open_webui.utils.response import get_response_delta_key, handle_responses_streaming_event

log = logging.getLogger(__name__)

# A dictionary to keep track of active tasks
tasks: dict[str, asyncio.Task] = {}
item_tasks = {}
response_streams: dict[str, dict] = {}


REDIS_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks'
REDIS_ITEM_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks:item'
REDIS_RESPONSE_STREAMS_KEY = f'{REDIS_KEY_PREFIX}:tasks:response_streams'
REDIS_PUBSUB_CHANNEL = f'{REDIS_KEY_PREFIX}:tasks:commands'
REDIS_PUBSUB_RECONNECT_INTERVAL = 1.0
REDIS_PUBSUB_MAX_RECONNECT_INTERVAL = 30.0


async def redis_task_command_listener(app):
    redis: Redis = app.state.redis
    reconnect_interval = REDIS_PUBSUB_RECONNECT_INTERVAL

    while True:
        pubsub = None
        try:
            # RedisCluster can't route a pubsub subscribe until initialize() fills its slot cache.
            await redis.initialize()

            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_PUBSUB_CHANNEL)
            reconnect_interval = REDIS_PUBSUB_RECONNECT_INTERVAL

            async for message in pubsub.listen():
                if message['type'] != 'message':
                    continue
                try:
                    command = JSONCodec.loads(message['data'])
                    if command.get('action') != 'stop':
                        continue

                    local_task = tasks.get(command.get('task_id'))
                    if local_task:
                        local_task.cancel()
                except Exception as e:
                    log.exception(f'Error handling distributed task command: {e}')
            log.warning('Redis task command listener stopped. Retrying.')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f'Redis task command listener failed. Retrying: {e}')
        finally:
            if pubsub:
                with suppress(Exception):
                    await pubsub.aclose()

        await asyncio.sleep(reconnect_interval)
        reconnect_interval = min(reconnect_interval * 2, REDIS_PUBSUB_MAX_RECONNECT_INTERVAL)


async def redis_task_heartbeat(app):
    redis: Redis = app.state.redis
    while True:
        await asyncio.sleep(REDIS_TASK_TTL / 4)
        try:
            pipe = redis.pipeline(transaction=False)
            for task_id in list(tasks):
                # EXPIRE cannot recreate a task already removed by cleanup.
                pipe.expire(f'{REDIS_TASKS_KEY}:{task_id}', REDIS_TASK_TTL)
            await pipe.execute()
        except Exception:
            log.exception('Redis task heartbeat failed')


### ------------------------------
### REDIS-ENABLED HANDLERS
### ------------------------------


async def redis_save_task(redis: Redis, task_id: str, item_id: str | None):
    pipe = redis.pipeline(transaction=False)
    pipe.set(f'{REDIS_TASKS_KEY}:{task_id}', '1', ex=REDIS_TASK_TTL or None)
    pipe.hset(REDIS_TASKS_KEY, task_id, item_id or '')
    if item_id:
        pipe.sadd(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
    await pipe.execute()


async def redis_cleanup_task(redis: Redis, task_id: str, item_id: str | None):
    pipe = redis.pipeline(transaction=False)
    pipe.delete(f'{REDIS_TASKS_KEY}:{task_id}')
    pipe.hdel(REDIS_TASKS_KEY, task_id)
    pipe.hdel(REDIS_RESPONSE_STREAMS_KEY, task_id)
    pipe.delete(f'{REDIS_RESPONSE_STREAMS_KEY}:{task_id}')
    if item_id:
        pipe.srem(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
    await pipe.execute()


async def redis_list_tasks(redis: Redis, item_id: str | None = None) -> list[str]:
    task_ids = list(
        await redis.smembers(f'{REDIS_ITEM_TASKS_KEY}:{item_id}')
        if item_id is not None
        else await redis.hkeys(REDIS_TASKS_KEY)
    )
    if not task_ids or REDIS_TASK_TTL == 0:
        return task_ids

    pipe = redis.pipeline(transaction=False)
    for task_id in task_ids:
        pipe.exists(f'{REDIS_TASKS_KEY}:{task_id}')

    active = []
    for task_id, exists in zip(task_ids, await pipe.execute()):
        if exists:
            active.append(task_id)
        else:
            task_item_id = item_id if item_id is not None else await redis.hget(REDIS_TASKS_KEY, task_id)
            await redis_cleanup_task(redis, task_id, task_item_id or None)
    return active


async def redis_send_command(redis: Redis, command: dict):
    command_json = dumps_bytes(command)
    # RedisCluster doesn't expose publish() directly, but the
    # PUBLISH command broadcasts across all cluster nodes server-side.
    if hasattr(redis, 'nodes_manager'):
        await redis.execute_command('PUBLISH', REDIS_PUBSUB_CHANNEL, command_json)
    else:
        await redis.publish(REDIS_PUBSUB_CHANNEL, command_json)


async def cleanup_task(redis, task_id: str, id=None):
    """
    Remove a completed or canceled task from the global `tasks` dictionary.
    """
    if redis:
        await redis_cleanup_task(redis, task_id, id)

    tasks.pop(task_id, None)  # Remove the task if it exists
    response_streams.pop(task_id, None)

    # If an ID is provided, remove the task from the item_tasks dictionary
    if id and task_id in item_tasks.get(id, []):
        item_tasks[id].remove(task_id)
        if not item_tasks[id]:  # If no tasks left for this ID, remove the entry
            item_tasks.pop(id, None)


def _finish_and_report(lifecycle, task):
    """Classify the finished Task, and put back the diagnostic we consumed.

    `classify_task` calls `task.exception()`, which marks the exception as
    retrieved and therefore SUPPRESSES asyncio's own "Task exception was never
    retrieved" warning. That warning is how an exception escaping cleanup --
    exactly the class this lifecycle exists to notice -- used to become visible
    at all, so consuming it silently would trade one observability win for an
    observability loss.

    The lifecycle row keeps only `raised`, with no text: its logger is trusted
    wholesale precisely because nothing free-form reaches it. The traceback goes
    to this module's ordinary logger instead, where it already went before.
    """
    outcome = turn_lifecycle.classify_task(task)
    lifecycle.finish(outcome)
    if outcome != turn_lifecycle.OUTCOME_RAISED:
        return
    try:
        exc = task.exception()
    except BaseException:  # noqa: BLE001
        return
    if exc is not None:
        log.error("chat task ended with an unhandled exception", exc_info=exc)


async def create_task(redis, coroutine, id=None, lifecycle=None, task_id=None):
    """
    Create a new asyncio task and add it to the global task dictionary.

    `lifecycle` is an optional `turn_lifecycle.Attempt` opened by the caller
    BEFORE this call. Its terminal belongs to the done callback below and to
    nothing else: a coroutine-level `finally` can pick `returned` and then be
    overturned by cleanup that raises, so the Task's final state is the first
    disposition that cannot change.
    """
    task_id = task_id or str(uuid4())

    try:
        task = asyncio.create_task(coroutine)  # Create the task
    except BaseException:
        # No owner was ever constructed. Close the unowned coroutine so this
        # rare path does not also emit "coroutine was never awaited", and let
        # neither the hygiene nor the observability mask the real failure.
        try:
            if inspect.iscoroutine(coroutine):
                coroutine.close()
        except BaseException:  # noqa: BLE001
            pass
        try:
            if lifecycle is not None:
                lifecycle.finish(turn_lifecycle.OUTCOME_NOT_STARTED)
        except BaseException:  # noqa: BLE001
            pass
        raise

    # THE LIFECYCLE CALLBACK GOES FIRST, and in its own guard. `task` is already
    # a strong reference and there is no await before the map assignment, so
    # registering here closes the otherwise-unhandled window where `tasks[...]`
    # fails and nothing owns the disposition. A callback added to an
    # already-finished Task is QUEUED, not run inline, so it cannot fire before
    # the bookkeeping below.
    if lifecycle is not None:
        try:
            task.add_done_callback(
                lambda t: _finish_and_report(lifecycle, t)
            )
        except BaseException:  # noqa: BLE001
            # The owner exists and may still return, raise or cancel -- we have
            # also lost the registry's ability to say whether it is active. The
            # terminal below unregisters immediately while the real Task keeps
            # running, so exposing len(_REGISTRY) after this point would be an
            # undercount. Poison the observation before consuming the lifecycle.
            try:
                turn_lifecycle.invalidate_owner_observation(
                    turn_lifecycle.OWNER_UNKNOWN_CALLBACK_REGISTRATION)
            except BaseException:  # noqa: BLE001 -- counting must not break owner
                pass
            # simply lost the ability to observe which. `unknown` is the honest
            # terminal; `not_started` would be a lie, because a Task was created.
            try:
                lifecycle.finish(turn_lifecycle.OUTCOME_UNKNOWN)
            except BaseException:  # noqa: BLE001
                pass

    # THE STRONG MAP BEFORE THE CLEANUP CALLBACK, which is what the contract
    # actually claimed. Built the other way round first: if cleanup-callback
    # registration raised, the Task existed with a lifecycle callback while
    # `tasks[task_id]` had never been set, so the lifecycle stayed honest but
    # application ownership did not.
    tasks[task_id] = task

    # Separate statement, separate guard: a lifecycle registration failure must
    # never skip application cleanup.
    task.add_done_callback(lambda t: asyncio.create_task(cleanup_task(redis, task_id, id)))

    # If an ID is provided, associate the task with that ID
    if id:
        if item_tasks.get(id):
            item_tasks[id].append(task_id)
        else:
            item_tasks[id] = [task_id]

    if redis:
        await redis_save_task(redis, task_id, id)

    return task_id, task


async def list_tasks(redis):
    """
    List all currently active task IDs.
    """
    if redis:
        return await redis_list_tasks(redis)
    return list(tasks.keys())


async def list_task_ids_by_item_id(redis, id):
    """
    List all tasks associated with a specific ID.
    """
    if redis:
        return await redis_list_tasks(redis, id)
    return list(item_tasks.get(id, []))


async def save_response_stream(
    redis,
    task_id: str | None,
    chat_id: str | None,
    message_id: str | None,
    output: list,
) -> int:
    """Save a snapshot and return its byte size; zero means it needs to be retried."""
    if not task_id or not chat_id or not message_id:
        return 0

    data = {
        'chat_id': chat_id,
        'message_id': message_id,
        'output': output,
    }

    if redis:
        snapshot = dumps_bytes(data) + b'\n'
        try:
            await redis.set(
                f'{REDIS_RESPONSE_STREAMS_KEY}:{task_id}',
                snapshot,
                ex=REDIS_RESPONSE_STREAM_TTL if REDIS_RESPONSE_STREAM_TTL > 0 else None,
            )
        except RedisError:
            return 0
        return len(snapshot)
    else:
        response_streams[task_id] = data
        return 0


async def append_response_stream(redis: Redis, task_id: str, delta: bytes, size: int) -> bool:
    """Append only to the snapshot we last wrote, including after a Redis retry."""
    try:
        return bool(
            await redis.eval(
                "if redis.call('strlen', KEYS[1]) ~= tonumber(ARGV[1]) then return 0 end "
                "redis.call('append', KEYS[1], ARGV[2]) "
                "if tonumber(ARGV[3]) > 0 then redis.call('expire', KEYS[1], ARGV[3]) end return 1",
                1,
                f'{REDIS_RESPONSE_STREAMS_KEY}:{task_id}',
                size,
                delta,
                REDIS_RESPONSE_STREAM_TTL,
            )
        )
    except RedisError:
        return False


def load_response_stream(value: str | bytes) -> dict:
    lines = value.split(b'\n' if isinstance(value, bytes) else '\n')
    snapshot, *deltas = (JSONCodec.loads(line) for line in lines if line)
    # Join adjacent text deltas before applying them, avoiding a full text copy per token.
    for _, events in groupby(deltas, key=get_response_delta_key):
        first, *rest = events
        delta = {**first, 'delta': first['delta'] + ''.join(event['delta'] for event in rest)}
        snapshot['output'], _ = handle_responses_streaming_event(delta, snapshot['output'])
    return snapshot


async def get_response_streams_by_chat_id(redis, chat_id: str) -> list[dict]:
    task_ids = await list_task_ids_by_item_id(redis, chat_id)
    if not task_ids:
        return []

    if redis:
        pipe = redis.pipeline(transaction=False)
        for task_id in task_ids:
            pipe.get(f'{REDIS_RESPONSE_STREAMS_KEY}:{task_id}')
        values = await pipe.execute()
        # Replies started on older instances still use the shared hash during an upgrade.
        if any(value is None for value in values):
            legacy = await redis.hmget(REDIS_RESPONSE_STREAMS_KEY, task_ids)
            values = [value if value is not None else old for value, old in zip(values, legacy)]
        streams = []
        for value in values:
            if not value:
                continue
            try:
                data = load_response_stream(value)
            except Exception:
                continue
            if data.get('chat_id') == chat_id:
                data['content'] = get_output_text(data['output'])
                streams.append(data)
        return streams

    return [
        {**stream, 'content': get_output_text(stream['output'])}
        for task_id in task_ids
        if (stream := response_streams.get(task_id)) and stream.get('chat_id') == chat_id
    ]


async def clear_response_stream(redis, task_id: str | None):
    if not task_id:
        return
    if redis:
        pipe = redis.pipeline(transaction=False)
        pipe.delete(f'{REDIS_RESPONSE_STREAMS_KEY}:{task_id}')
        pipe.hdel(REDIS_RESPONSE_STREAMS_KEY, task_id)
        await pipe.execute()
    else:
        response_streams.pop(task_id, None)


async def stop_task(redis, task_id: str):
    """
    Cancel a running task and remove it from the global task list.
    """
    if redis:
        # Look up the item_id before cleanup so we can remove the set entry too
        item_id = await redis.hget(REDIS_TASKS_KEY, task_id)
        # PUBSUB: All instances check if they have this task, and stop if so.
        await redis_send_command(
            redis,
            {
                'action': 'stop',
                'task_id': task_id,
            },
        )
        # Always clean Redis directly — hdel/srem are idempotent, safe even
        # if the done_callback on the owning process also fires cleanup.
        await redis_cleanup_task(redis, task_id, item_id or None)
        return {'status': True, 'message': f'Task {task_id} stopped.'}

    task = tasks.pop(task_id, None)
    if not task:
        return {'status': False, 'message': f'Task with ID {task_id} not found.'}

    task.cancel()  # Request task cancellation
    try:
        await task  # Wait for the task to handle the cancellation
    except asyncio.CancelledError:
        # Task successfully canceled
        return {'status': True, 'message': f'Task {task_id} successfully stopped.'}

    if task.cancelled() or task.done():
        return {'status': True, 'message': f'Task {task_id} successfully cancelled.'}

    return {'status': True, 'message': f'Cancellation requested for {task_id}.'}


async def stop_item_tasks(redis: Redis, item_id: str):
    """
    Stop all tasks associated with a specific item ID.
    """
    task_ids = await list_task_ids_by_item_id(redis, item_id)
    if not task_ids:
        return {'status': True, 'message': f'No tasks found for item {item_id}.'}

    # Cleanup mutates the local task list while cancellation is awaited.
    for task_id in list(task_ids):
        # A task that already finished needs no stopping; continue with the rest.
        await stop_task(redis, task_id)

    return {'status': True, 'message': f'All tasks for item {item_id} stopped.'}


async def has_active_tasks(redis, chat_id: str) -> bool:
    """Check if a chat has any active tasks."""
    task_ids = await list_task_ids_by_item_id(redis, chat_id)
    return len(task_ids) > 0
