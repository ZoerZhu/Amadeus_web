from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..code_tasks.opencode_runner import (
    drain_opencode_task,
    reply_opencode_question,
    stream_opencode_task,
)
from ..code_tasks.task_hub import code_task_hub
from ..domain import (
    CodeTaskQuestionReplyRequest,
    CodeTaskStreamRequest,
    CodeTaskSyncRequest,
)
from ..event_stream import sse

router = APIRouter(prefix="/api/code-tasks", tags=["code-tasks"])


async def stream_code_task_events(*, replay: bool):
    async for record in code_task_hub.subscribe_all(replay=replay):
        yield sse(record.event, record.payload)


@router.post("/stream")
async def code_task_stream(request: CodeTaskStreamRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_opencode_task(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/question/reply")
async def code_task_question_reply(request: CodeTaskQuestionReplyRequest) -> dict:
    try:
        return await reply_opencode_question(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@router.post("/sync")
async def sync_code_tasks(request: CodeTaskSyncRequest) -> dict:
    synced = await code_task_hub.sync_snapshots(
        [task.model_dump(by_alias=True) for task in request.tasks]
    )
    return {"ok": True, "tasks": synced}


@router.get("/events")
async def code_task_events(replay: bool = False) -> StreamingResponse:
    return StreamingResponse(
        stream_code_task_events(replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
