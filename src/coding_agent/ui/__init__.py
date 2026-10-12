"""NEXUS console: the Textual front end for the coding agent.

Layering (mirrors the requested architecture; existing backend packages keep
their roles):
    ui/      this package - layout, widgets, theme, animations, palette
    agent/   event-driven bridge (controller + typed events + state machine)
    tools/   -> coding_agent.core.tools  (filesystem / shell / git via services / web search)
    models/  -> coding_agent.core.llm    (llm_client + streaming providers)
    context/ -> core.context_builder + services.compactor (context_manager + token tracker)
"""
from coding_agent.ui.app import CodeAgentApp, run_tui

__all__ = ["CodeAgentApp", "run_tui"]
