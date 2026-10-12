"""Context-window management: token estimation and history compaction.

Compaction replaces all-but-the-recent conversation history with a single
LLM-written summary. The summary is injected as a *system* message (never a
user/assistant turn) so providers never see role-alternation or orphaned
tool-result problems, and it is persisted in session metadata so later turns
and new processes keep it.
"""
import json

from coding_agent.config import settings
from coding_agent.core.llm.base import LLMMessage, LLMProvider

SUMMARY_MARKER = "[Earlier conversation summary"

SUMMARIZE_PROMPT = """\
Condense the following coding-agent conversation transcript into a compact
briefing for a continuation of the same session. Keep: the user's goals and
decisions, files created/modified (paths) and what changed, important errors
and their fixes, and any open TODOs. Drop tool output verbatim noise. Reply
with only the briefing, under 300 words.

Transcript:
"""


def estimate_tokens(text: str) -> int:
    """Cheap chars/4 heuristic — no tokenizer downloads, close enough for
    budget thresholds. Provider usage numbers are used for exact display."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(history: list[LLMMessage]) -> int:
    total = 0
    for m in history:
        total += 4  # per-message overhead (role framing)
        total += estimate_tokens(m.content or "")
        for tc in m.tool_calls or []:
            total += estimate_tokens(json.dumps(tc.get("arguments", {}), default=str))
    return total


def serialize_for_summary(messages: list[LLMMessage]) -> str:
    """Readable transcript for the summarizer; long payloads abbreviated."""
    lines: list[str] = []
    for m in messages:
        if m.role == "tool":
            content = m.content or ""
            if len(content) > 400:
                n_chars = len(content)
                content = content[:200] + f" ...[{n_chars} chars]... " + content[-100:]
            lines.append(f"tool result ({m.tool_call_id}): {content}")
        elif m.tool_calls:
            calls = "; ".join(
                f"{tc['name']}({json.dumps(tc.get('arguments', {}), default=str)[:200]})"
                for tc in m.tool_calls
            )
            text = (m.content or "").strip()
            lines.append(f"assistant [calls {calls}]" + (f": {text}" if text else ""))
        else:
            label = "user" if m.role == "user" else m.role
            content = (m.content or "").strip()
            if len(content) > 1200:
                content = content[:600] + f" ...[{len(content)} chars]... " + content[-300:]
            lines.append(f"{label}: {content}")
    return "\n".join(lines)


def split_history(
    history: list[LLMMessage], keep_recent: int | None = None
) -> tuple[list[LLMMessage], list[LLMMessage]] | None:
    """Split into (older, kept). `kept` starts at a user message so no orphaned
    assistant(tool_calls)/tool pairs are torn apart. Returns None when there is
    nothing safely compactable."""
    keep_recent = keep_recent if keep_recent is not None else settings.COMPACT_KEEP_RECENT
    split = len(history) - keep_recent
    while split > 0 and history[split].role != "user":
        split -= 1
    # Need at least one message to summarize; system messages stay out of both.
    if split <= 0 or not any(m.role != "system" for m in history[:split]):
        return None
    return history[:split], history[split:]


async def summarize(older: list[LLMMessage], provider: LLMProvider,
                    model: str | None = None) -> str:
    transcript = serialize_for_summary(older)
    response = await provider.complete(
        messages=[LLMMessage(role="user", content=SUMMARIZE_PROMPT + transcript)],
        tools=None,  # the summarizer must never call tools
        model=model,
        temperature=0.3,
        max_tokens=1200,
    )
    return (response.content or "").strip()


def summary_message(summary_text: str, summarized_count: int) -> LLMMessage:
    return LLMMessage(
        role="system",
        content=(
            f"{SUMMARY_MARKER} — {summarized_count} earlier messages condensed to save "
            "context; treat as established history]\n\n" + summary_text
        ),
    )
