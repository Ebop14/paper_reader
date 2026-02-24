import asyncio
import json
import time
from typing import AsyncGenerator

# In-memory task registry
task_registry: dict[str, dict] = {}


def create_task(task_id: str, total_chunks: int = 0, stages: list[str] | None = None):
    task_registry[task_id] = {
        "task_id": task_id,
        "status": "running",
        "progress": 0.0,
        "current_chunk": 0,
        "total_chunks": total_chunks,
        "message": "Starting...",
        "updated_at": time.time(),
    }
    if stages:
        task_registry[task_id]["stages"] = stages
        task_registry[task_id]["stage"] = stages[0]
        task_registry[task_id]["stage_progress"] = 0.0


def update_task(task_id: str, **kwargs):
    if task_id not in task_registry:
        return
    task = task_registry[task_id]
    task.update(kwargs)
    if task["total_chunks"] > 0:
        task["progress"] = task["current_chunk"] / task["total_chunks"]
    task["updated_at"] = time.time()


async def sse_stream(task_id: str) -> AsyncGenerator[str, None]:
    """Yield SSE events until the task completes or fails."""
    last_update = 0.0
    while True:
        task = task_registry.get(task_id)
        if task is None:
            yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
            return

        if task["updated_at"] > last_update:
            last_update = task["updated_at"]
            yield f"data: {json.dumps(task, default=str)}\n\n"

        if task["status"] in ("completed", "failed"):
            return

        await asyncio.sleep(0.5)
