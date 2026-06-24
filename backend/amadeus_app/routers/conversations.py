"""Conversation CRUD routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from .._common import require_storage
from ..domain import ConversationCreateRequest
from ..storage import DEFAULT_USER_ID

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def conversations() -> dict:
    return {"conversations": await require_storage().list_conversations(user_id=DEFAULT_USER_ID)}


@router.post("")
async def create_conversation(request: ConversationCreateRequest) -> dict:
    conversation = await require_storage().create_conversation(
        user_id=DEFAULT_USER_ID,
        title=request.title,
        persona_id=request.persona_id,
        mode=request.mode,
    )
    return {"conversation": conversation}


@router.get("/{conversation_id}")
async def conversation(conversation_id: UUID) -> dict:
    item = await require_storage().get_conversation(user_id=DEFAULT_USER_ID, conversation_id=conversation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation": item}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: UUID) -> dict:
    deleted = await require_storage().delete_conversation(user_id=DEFAULT_USER_ID, conversation_id=conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}
