# backend/amadeus_app/orchestrator/context_window.py
"""Context window management for the agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSummary:
    """Result of context trimming operation."""
    messages: list[dict[str, Any]]
    trimmed_count: int
    estimated_tokens: int
    summary_text: str = ""


# Rough token estimation: ~4 characters per token for English text.
_CHARS_PER_TOKEN = 4
# Reserve tokens for system prompt + response generation
_RESPONSE_RESERVE = 2000
# Minimum messages to always keep (user + most recent exchange)
_MIN_KEEP = 4


def estimate_token_count(messages: list[dict[str, Any]]) -> int:
    """Estimate the token count of a message list."""
    total = 0
    for msg in messages:
        role = str(msg.get("role") or "")
        total += len(role) // _CHARS_PER_TOKEN + 2
        content = str(msg.get("content") or "")
        total += len(content) // _CHARS_PER_TOKEN
        tool_calls = msg.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function") or {}
                    total += len(str(func.get("name") or "")) // _CHARS_PER_TOKEN
                    total += len(str(func.get("arguments") or "")) // _CHARS_PER_TOKEN
                total += 4
        name = str(msg.get("name") or "")
        if name:
            total += len(name) // _CHARS_PER_TOKEN
    return total


def _summarize_old_messages(messages: list[dict[str, Any]]) -> str:
    """Generate a compact summary of older messages being trimmed."""
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "unknown")
        content = str(msg.get("content") or "")
        if role == "user":
            parts.append(f"User asked: {content[:200]}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                names = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function") or {}
                        names.append(str(func.get("name") or ""))
                parts.append(f"Assistant called: {', '.join(names)}")
            else:
                parts.append(f"Assistant said: {content[:150]}")
        elif role == "tool":
            name = str(msg.get("name") or "tool")
            parts.append(f"Tool {name} result: {content[:150]}")
    return "[Context Summary] " + " | ".join(parts)[:2000]


def trim_context(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    system_prompt: str = "",
) -> list[dict[str, Any]]:
    """Trim the message list to fit within the token budget.

    Strategy:
    1. Always keep the first user message (original task).
    2. Always keep the most recent _MIN_KEEP messages.
    3. If still over budget, trim middle messages and insert a summary.
    4. Never trim below _MIN_KEEP messages.
    """
    if not messages:
        return []

    system_tokens = len(system_prompt) // _CHARS_PER_TOKEN
    budget = max_tokens - system_tokens - _RESPONSE_RESERVE
    if budget < 200:
        budget = 200

    current_tokens = estimate_token_count(messages)
    if current_tokens <= budget:
        return list(messages)

    first_user_idx = 0
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            first_user_idx = i
            break

    keep_start = [messages[first_user_idx]] if first_user_idx < len(messages) else []
    keep_end_count = min(_MIN_KEEP, len(messages) - first_user_idx - 1)
    if keep_end_count < 0:
        keep_end_count = 0
    keep_end = messages[-keep_end_count:] if keep_end_count > 0 else []

    middle_start = first_user_idx + 1
    middle_end = len(messages) - keep_end_count
    middle = messages[middle_start:middle_end]

    if not middle:
        return keep_start + keep_end

    test_msgs = keep_start + middle + keep_end
    if estimate_token_count(test_msgs) <= budget:
        return test_msgs

    summary_inserted = False
    while middle and estimate_token_count(keep_start + middle + keep_end) > budget:
        removed = middle.pop(0)
        if not summary_inserted:
            summary_inserted = True

    result = keep_start
    if summary_inserted:
        summary_text = _summarize_old_messages(messages[first_user_idx + 1:middle_start + len(middle)])
        if summary_text:
            result.append({
                "role": "system",
                "content": summary_text,
            })
    result.extend(middle)
    result.extend(keep_end)

    _TRUNCATION_MARKER = "...[truncated for context window]"
    while estimate_token_count(result) > budget and len(result) > _MIN_KEEP:
        longest_idx = -1
        longest_len = 0
        for i, msg in enumerate(result):
            if msg.get("role") == "tool":
                content = str(msg.get("content") or "")
                # Skip tool messages that have already been truncated to
                # avoid re-truncating them in an infinite loop.
                if _TRUNCATION_MARKER in content:
                    continue
                content_len = len(content)
                if content_len > longest_len:
                    longest_len = content_len
                    longest_idx = i
        if longest_idx >= 0 and longest_len > 500:
            content = str(result[longest_idx].get("content") or "")
            result[longest_idx] = {
                **result[longest_idx],
                "content": content[:500] + "\n" + _TRUNCATION_MARKER,
            }
        else:
            break

    return result
