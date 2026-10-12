"""Agent status state machine: events -> the live state pill.

Pure and UI-free on purpose: any frontend can render AgentStatus however it
likes. The canonical labels are the spec strings ("[◉ READING]" etc.) so every
surface shows identical wording.
"""
from enum import StrEnum

from coding_agent.agent.events import (
    AgentEvent,
    Notice,
    StreamDelta,
    ToolFinished,
    ToolStarted,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
)

# Tool name -> which high-level activity a call represents.
READING_TOOLS = {"read", "list", "glob"}
SEARCHING_TOOLS = {"grep", "web_search"}
EDITING_TOOLS = {"write", "edit"}


class AgentStatus(StrEnum):
    ONLINE = "online"
    ANALYZING = "analyzing"
    SEARCHING = "searching"
    READING = "reading"
    PLANNING = "planning"
    EXECUTING = "executing"
    EDITING = "editing"
    COMPLETE = "complete"
    ERROR = "error"


# Canonical display form: "[● ONLINE]", "[◉ ANALYZING]", "[✓ COMPLETE]", "[× ERROR]"
GLYPH: dict[AgentStatus, str] = {
    AgentStatus.ONLINE: "●",
    AgentStatus.ANALYZING: "◉",
    AgentStatus.SEARCHING: "◉",
    AgentStatus.READING: "◉",
    AgentStatus.PLANNING: "◉",
    AgentStatus.EXECUTING: "◉",
    AgentStatus.EDITING: "◉",
    AgentStatus.COMPLETE: "✓",
    AgentStatus.ERROR: "×",
}

# States where work is in flight — pill animates; others are steady.
ACTIVE_STATES = {
    AgentStatus.ANALYZING, AgentStatus.SEARCHING, AgentStatus.READING,
    AgentStatus.PLANNING, AgentStatus.EXECUTING, AgentStatus.EDITING,
}


def label(status: AgentStatus) -> str:
    return f"[{GLYPH[status]} {status.name}]"


def derive_status(event: AgentEvent, current: AgentStatus = AgentStatus.ONLINE,
                  planning: bool = False) -> AgentStatus:
    """Next status given an event. `planning` marks /plan turns so research
    reads as PLANNING rather than plain ANALYZING."""
    if isinstance(event, TurnStarted):
        return AgentStatus.PLANNING if event.planning else AgentStatus.ANALYZING
    if isinstance(event, ToolStarted):
        name = event.name
        if name in READING_TOOLS:
            return AgentStatus.READING
        if name in SEARCHING_TOOLS:
            return AgentStatus.SEARCHING
        if name in EDITING_TOOLS:
            return AgentStatus.EDITING
        return AgentStatus.PLANNING if planning else AgentStatus.EXECUTING
    if isinstance(event, (ToolFinished, StreamDelta)):
        if not planning and current is AgentStatus.PLANNING:
            return AgentStatus.ANALYZING
        return current  # keep showing what we were doing between steps
    if isinstance(event, TurnFinished):
        return AgentStatus.COMPLETE
    if isinstance(event, TurnFailed):
        return AgentStatus.ERROR
    if isinstance(event, TurnCancelled):
        return AgentStatus.ONLINE
    if isinstance(event, Notice) and event.level == "error":
        return AgentStatus.ERROR
    return current
