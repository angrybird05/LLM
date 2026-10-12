"""Headless event-driven agent layer.

This package adapts the battle-tested backend (ChatService's agentic loop,
permission system, hooks, checkpoints) into a stream of typed events that any
frontend can react to — the Textual UI in `coding_agent.ui` is the first one.

The layer owns NO agent logic: it drives `ChatService.stream_chat`, translates
raw provider/tool dicts into immutable AgentEvent dataclasses, tracks turn
metrics (time-to-first-token, tool durations) and exposes cooperative
cancellation. The UI subscribes; it never reaches into the loop.
"""
from coding_agent.agent.controller import PLAN_SUFFIX, AgentController
from coding_agent.agent.events import (
    AgentEvent,
    Notice,
    SessionResolved,
    StreamDelta,
    ToolFinished,
    ToolStarted,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
)
from coding_agent.agent.state_manager import AgentStatus, derive_status

__all__ = [
    "AgentController",
    "AgentEvent",
    "AgentStatus",
    "Notice",
    "PLAN_SUFFIX",
    "SessionResolved",
    "StreamDelta",
    "ToolFinished",
    "ToolStarted",
    "TurnCancelled",
    "TurnFailed",
    "TurnFinished",
    "TurnStarted",
    "derive_status",
]
