# tasks.py
import asyncio
import inspect
import json
import logging
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import Request
from redis.asyncio import Redis

from open_webui.env import REDIS_KEY_PREFIX
from open_webui.utils import turn_lifecycle

log = logging.getLogger(__name__)

# A dictionary to keep track of active tasks
tasks: Dict[str, asyncio.Task] = {}
item_tasks = {}


REDIS_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks'
REDIS_ITEM_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks:item'
REDIS_PUBSUB_CHANNEL = f'{REDIS_KEY_PREFIX}:tasks:commands'


async def redis_task_command_listener(app):
    redis: Redis = app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_PUBSUB_CHANNEL)

    async for message in pubsub.listen():
        if message['type'] != 'message':
            continue
        try:
            command = json.loads(message['data'])
            if command.get('action') == 'stop':
                task_id = command.get('task_id')
                local_task = tasks.get(task_id)
                if local_task:
                    local_task.cancel()
        except Exception as e:
            log.exception(f'Error handling distributed task command: {e}')


### ------------------------------
### REDIS-ENABLED HANDLERS
### ------------------------------


async def redis_save_task(redis: Redis, task_id: str, item_id: Optional[str]):
    pipe = redis.pipeline()
    pipe.hset(REDIS_TASKS_KEY, task_id, item_id or '')
    if item_id:
        pipe.sadd(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
    await pipe.execute()


async def redis_cleanup_task(redis: Redis, task_id: str, item_id: Optional[str]):
    pipe = redis.pipeline()
    pipe.hdel(REDIS_TASKS_KEY, task_id)
    if item_id:
        pipe.srem(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
        await pipe.execute()
        # Remove the set key entirely if no tasks remain for this item
        if await redis.scard(f'{REDIS_ITEM_TASKS_KEY}:{item_id}') == 0:
            await redis.delete(f'{REDIS_ITEM_TASKS_KEY}:{item_id}')
    else:
        await pipe.execute()


async def redis_list_tasks(redis: Redis) -> List[str]:
    return list(await redis.hkeys(REDIS_TASKS_KEY))


async def redis_list_item_tasks(redis: Redis, item_id: str) -> List[str]:
    return list(await redis.smembers(f'{REDIS_ITEM_TASKS_KEY}:{item_id}'))


async def redis_send_command(redis: Redis, command: dict):
    command_json = json.dumps(command)
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


async def create_task(redis, coroutine, id=None, lifecycle=None):
    """
    Create a new asyncio task and add it to the global task dictionary.

    `lifecycle` is an optional `turn_lifecycle.Attempt` opened by the caller
    BEFORE this call. Its terminal belongs to the done callback below and to
    nothing else: a coroutine-level `finally` can pick `returned` and then be
    overturned by cleanup that raises, so the Task's final state is the first
    disposition that cannot change.
    """
    task_id = str(uuid4())  # Generate a unique ID for the task

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
        return await redis_list_item_tasks(redis, id)
    return item_tasks.get(id, [])


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

    for task_id in task_ids:
        result = await stop_task(redis, task_id)
        if not result['status']:
            return result  # Return the first failure

    return {'status': True, 'message': f'All tasks for item {item_id} stopped.'}


async def has_active_tasks(redis, chat_id: str) -> bool:
    """Check if a chat has any active tasks."""
    task_ids = await list_task_ids_by_item_id(redis, chat_id)
    return len(task_ids) > 0


async def get_active_chat_ids(redis, chat_ids: List[str]) -> List[str]:
    """Filter a list of chat_ids to only those with active tasks."""
    active = []
    for chat_id in chat_ids:
        if await has_active_tasks(redis, chat_id):
            active.append(chat_id)
    return active
