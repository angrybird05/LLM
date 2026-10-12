"""Typed events emitted by the AgentController.

Every event is an immutable snapshot of one step of a turn. The UI reacts to
these and to nothing else — it never inspects provider internals. Keep the set
small and semantic; renderers derive everything (glyphs, colors, states) from
these fields alone.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TurnStarted:
    """A turn is beginning: `text` is the user prompt, `planning` marks a
    read-only /plan research turn."""

    text: str
    planning: bool = False


@dataclass(frozen=True, slots=True)
class UsageUpdated:
    """Cumulative token metering after one LLM call inside a turn.

    Emitted between StreamDelta/Tool events so the UI's token and context
    rows move while the turn is still running; TurnFinished stays the
    authoritative final report."""

    input_tokens: int
    output_tokens: int
    context_tokens: int | None = None
    iterations: int = 0


@dataclass(frozen=True, slots=True)
class SessionResolved:
    """stream_chat created or adopted a session — its id applies from here on."""

    session_id: str


@dataclass(frozen=True, slots=True)
class StreamDelta:
    """A fragment of assistant text arrived (streaming)."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolStarted:
    """The model asked for a tool and execution has begun.

    `repeat` marks an repeated identical call earlier in the same turn —
    renderers de-noise those instead of printing full-weight cards."""

    name: str
    arguments: dict[str, Any]
    repeat: bool = False


@dataclass(frozen=True, slots=True)
class ToolFinished:
    """A tool call completed. `ok` False covers both tool errors and
    permission declines (`result` records the structured payload either way)."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    ok: bool
    duration_s: float


@dataclass(frozen=True, slots=True)
class Notice:
    """A one-line message for the transcript: info | success | warn | error."""

    level: str
    text: str


@dataclass(frozen=True, slots=True)
class TurnFinished:
    """The agentic loop ended normally with a final assistant message."""

    session_id: str | None
    message: str
    iterations: int
    usage: dict[str, int] = field(default_factory=dict)
    context_tokens: int | None = None
    elapsed_s: float = 0.0
    ttft_s: float | None = None  # time to first token/event


@dataclass(frozen=True, slots=True)
class TurnFailed:
    """The whole turn failed; `kind` is classify_provider_error's verdict:
    credits | too_long | provider | internal."""

    kind: str
    detail: str
    elapsed_s: float = 0.0


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    """The user interrupted the turn (Ctrl+C)."""

    elapsed_s: float = 0.0


AgentEvent = (
    TurnStarted | SessionResolved | StreamDelta | ToolStarted | ToolFinished
    | Notice | UsageUpdated | TurnFinished | TurnFailed | TurnCancelled
)
