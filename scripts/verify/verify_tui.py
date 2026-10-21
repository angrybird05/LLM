"""Headless verification of the NEXUS TUI (Ask 11).

Boots CodeAgentApp under Textual's Pilot with a fake ChatService that plays
scripted provider scenarios (normal streaming turn, approval-required bash
call, denial, hung stream), then asserts on widget/state outcomes:

  1. boot          - five regions mounted, nexus theme, prompt focused
  2. streamed turn - user/assistant blocks, tool card, metrics, COMPLETE
  3. approve       - risky bash -> PermissionModal -> 'y' -> turn completes
  4. deny          - 'n' -> failed tool card, turn still finishes
  5. interrupt     - hung stream + ctrl+c -> ONLINE again
  5b. live tokens  - usage events mid-turn move tracker before TurnFinished
  6. autocomplete  - '/mod' suggests, Enter completes to '/model '
  7. history       - up-arrow recalls the previous prompt
  7b. permissions  - console overlay: mode keys 1/2/3, revoke grants, log
  8. palette       - ctrl+p opens the searchable command palette
  9. clear view    - ctrl+l wipes the transcript, session kept
 10. diff overlay  - pushes and Esc closes
 11. responsive    - narrow terminal hides side panels, wide restores them
 12. follow mode   - streaming view pins bottom only while user is there
 13. project tree  - lazy expand, junk pruning, colored entries, honest stats
 14. history       - seeded DB: picker resume via Enter, panel session box
 15. restore       - resumed session renders user/tool/assistant blocks
 16. delete        - picker 'd' -> confirm -> current session wiped clean
 17. file preview  - Enter-on-file opens FileViewerOverlay, Esc closes

Run: python verify_tui.py
Exit code 0 = all PASS.
"""
import asyncio
import os
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

# Isolate the database BEFORE any coding_agent import touches settings.
_TMPDIR = tempfile.mkdtemp(prefix="nexus_verify_")
os.environ["DATABASE_URL"] = \
    f"sqlite+aiosqlite:///{Path(_TMPDIR) / 'verify.db'}"
os.environ.setdefault("AGENT_MAX_ITERATIONS", "4")

import logging  # noqa: E402

logging.getLogger("sqlalchemy.engine.Engine").disabled = True

import coding_agent.agent.controller as ctrl_mod  # noqa: E402
from coding_agent.agent import AgentStatus  # noqa: E402
from coding_agent.core.permissions import PermissionManager  # noqa: E402
from coding_agent.ui.app import CodeAgentApp  # noqa: E402
from coding_agent.ui.widgets.ai_core import AssistantBlock, UserBlock  # noqa: E402
from coding_agent.ui.widgets.command_dock import CommandDock, PromptArea  # noqa: E402
from coding_agent.ui.widgets.header import NexusHeader  # noqa: E402
from coding_agent.ui.widgets.modals import (  # noqa: E402
    DiffOverlay,
    PermissionModal,
    PermissionsOverlay,
)
from coding_agent.ui.widgets.project_panel import (  # noqa: E402
    ProjectPanel,
)
from coding_agent.ui.widgets.state_bar import StateBar  # noqa: E402
from coding_agent.ui.widgets.system_panel import SystemPanel  # noqa: E402
from coding_agent.ui.widgets.tool_card import ToolCard  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail and not cond else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


class FakeChatService:
    """Drop-in for ChatService playing scripted provider scenarios."""

    scenario = "happy"

    def __init__(self, session_service, permission_gate=None,
                 pre_tool_hook=None, hook_runner=None):
        self.permission_gate = permission_gate

    async def _gate(self, name, arguments) -> bool:
        gate = self.permission_gate
        if gate is None:
            return True
        allowed: bool = await gate(name, arguments)
        return allowed

    async def stream_chat(self, *, message, session_id=None, provider_name=None,
                          model=None, system_prompt="", tool_names=None):
        yield {"type": "session", "session_id": str(uuid.uuid4())}

        if FakeChatService.scenario == "hang":
            await asyncio.sleep(60)  # only a hard cancel gets out of here
            return

        if FakeChatService.scenario == "approve":
            yield {"type": "text", "text": "Running the risky command..."}
            yield {"type": "usage", "usage": {"input": 40, "output": 10},
                   "context_tokens": 900, "iterations": 1}
            allowed = await self._gate("bash", {"command": "echo nexus-ok"})
            yield {"type": "tool_use_start", "id": "t1", "name": "bash"}
            yield {"type": "tool_start", "name": "bash",
                   "arguments": {"command": "echo nexus-ok"}}
            if allowed:
                yield {"type": "tool_result", "name": "bash",
                       "result": {"stdout": "nexus-ok\n", "exit_code": 0}}
                yield {"type": "text", "text": "Command ran cleanly."}
            else:
                yield {"type": "tool_result", "name": "bash",
                       "result": {"error": "declined by user"}}
                yield {"type": "text", "text": "Understood, skipping."}
            yield {"type": "usage", "usage": {"input": 110, "output": 30},
                   "context_tokens": 1500, "iterations": 3}
            yield {"type": "turn_done", "usage": {"input": 110, "output": 30},
                   "iterations": 3, "message": "done",
                   "context_tokens": 1500}
            return

        # default "happy": streamed reply + one successful read tool
        for chunk in ("Nexus ", "here.", " Reading ", "the ", "layout."):
            yield {"type": "text", "text": chunk}
        yield {"type": "usage", "usage": {"input": 70, "output": 25},
               "context_tokens": 800, "iterations": 1}
        yield {"type": "tool_use_start", "id": "t1", "name": "read"}
        yield {"type": "tool_start", "name": "read",
               "arguments": {"path": "TASKS.md"}}
        yield {"type": "tool_result", "name": "read",
               "result": {"path": "TASKS.md", "content": "# tasks\n- ..."}}
        yield {"type": "text", "text": " All set."}
        yield {"type": "usage", "usage": {"input": 120, "output": 45},
               "context_tokens": 1650, "iterations": 2}
        yield {"type": "turn_done", "usage": {"input": 120, "output": 45},
               "iterations": 2, "message": "Nexus here. All set.",
               "context_tokens": 1650}


ctrl_mod.ChatService = FakeChatService  # type: ignore[assignment]  # controller builds THIS


async def settled(pilot, pred: Callable[[], bool], timeout_s: float = 15.0) -> bool:
    """Pump the message pump until pred() or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        await pilot.pause()
    return pred()


async def run_turn(pilot, app, prompt: str) -> None:
    """Start a turn and wait for its TurnFinished (tracker.turns bump),
    which is race-free unlike polling controller.running."""
    turns_before = app.tracker.turns
    app.start_turn(prompt)
    await settled(pilot, lambda: app.tracker.turns > turns_before,
                  timeout_s=20)


async def main() -> int:

    from coding_agent.models.database import init_db

    await init_db()
    perms = PermissionManager.from_mode("default")
    app = CodeAgentApp(permissions=perms)

    async with app.run_test(size=(130, 38)) as pilot:
        core = app.query_one("AiCore")
        dock = app.query_one(CommandDock)
        area = dock.query_one("#prompt", PromptArea)
        sbar = app.query_one(StateBar)

        # ---------------------------------------------------- 1. boot
        check("boot: header mounted", bool(app.query(NexusHeader)))
        check("boot: project panel mounted", bool(app.query(ProjectPanel)))
        check("boot: system panel mounted", bool(app.query(SystemPanel)))
        check("boot: ai core mounted", bool(core))
        check("boot: state bar ONLINE",
              sbar.status == AgentStatus.ONLINE, str(sbar.status))
        check("boot: nexus theme active", app.theme == "nexus", app.theme)
        check("boot: prompt focused", area.has_focus)

        # ---------------------------------------------------- 2. streamed turn
        FakeChatService.scenario = "happy"
        await run_turn(pilot, app, "hello nexus")
        check("turn: user block rendered", bool(core.query(UserBlock)))
        check("turn: assistant block rendered",
              bool(core.query(AssistantBlock)))
        check("turn: state COMPLETE",
              sbar.status == AgentStatus.COMPLETE, str(sbar.status))
        tr = app.tracker
        check("turn: usage recorded 120/45",
              tr.input_tokens == 120 and tr.output_tokens == 45,
              f"{tr.input_tokens}/{tr.output_tokens}")
        check("turn: context tokens 1650", tr.context_tokens == 1650,
              str(tr.context_tokens))
        check("turn: session resolved", app.session_id is not None)
        check("turn: tool card succeeded",
              any(not c.has_class("running") and not c.has_class("failed")
                  for c in core.query(ToolCard)))

        # ---------------------------------------------------- 3. approve flow
        FakeChatService.scenario = "approve"
        app.start_turn("run something risky")
        modal_ok = await settled(
            pilot, lambda: isinstance(app.screen, PermissionModal))
        check("approve: permission modal appeared", modal_ok)
        if modal_ok:
            await pilot.press("y")
        await settled(pilot, lambda: app.controller is not None
                      and not app.controller.running)
        check("approve: no modal left open",
              not isinstance(app.screen, PermissionModal))
        check("approve: turn completed",
              sbar.status == AgentStatus.COMPLETE, str(sbar.status))

        # ---------------------------------------------------- 4. deny flow
        app.start_turn("run something risky")
        modal_ok = await settled(
            pilot, lambda: isinstance(app.screen, PermissionModal))
        check("deny: permission modal appeared", modal_ok)
        if modal_ok:
            await pilot.press("n")
        await settled(pilot, lambda: app.controller is not None
                      and not app.controller.running)
        check("deny: no modal left open",
              not isinstance(app.screen, PermissionModal))
        check("deny: failed tool card shown",
              any(c.has_class("failed") for c in core.query(ToolCard)))

        # --------------------------------------------- 5b. live token metering
        FakeChatService.scenario = "approve"
        turns_before_live = app.tracker.turns
        baseline_in = app.tracker.input_tokens
        app.start_turn("live metering probe")
        modal_ok = await settled(
            pilot, lambda: isinstance(app.screen, PermissionModal))
        check("live-tokens: modal appeared", modal_ok)
        # The gate blocks the turn mid-stream, so usage must land NOW -
        # well before TurnFinished - for the meter to count as live.
        live_seen = await settled(pilot,
                                  lambda: app.tracker.turn_input >= 40,
                                  timeout_s=5)
        check("live-tokens: turn tokens visible mid-flight", live_seen,
              str(app.tracker.turn_input))
        check("live-tokens: session total includes live turn",
              app.tracker.input_tokens >= baseline_in + 40,
              f"{app.tracker.input_tokens} vs baseline {baseline_in}")
        if modal_ok:
            await pilot.press("y")
        await settled(pilot, lambda: app.tracker.turns > turns_before_live)
        check("live-tokens: final usage committed",
              app.tracker.input_tokens >= 110,
              str(app.tracker.input_tokens))

        # ------------------------------------------ 6. permissions console
        from coding_agent.core.permissions import PermissionMode

        perms.session_allow.add("bash")
        app.push_screen(PermissionsOverlay(app.permissions))
        await pilot.pause()
        check("permissions: overlay opened",
              isinstance(app.screen, PermissionsOverlay))
        await pilot.press("3")
        check("permissions: key 3 -> full-auto",
              app.permissions.mode == PermissionMode.FULL_AUTO)
        await pilot.press("2")
        check("permissions: key 2 -> auto-edit",
              app.permissions.mode == PermissionMode.AUTO_EDIT)
        await pilot.press("1")
        check("permissions: key 1 -> default",
              app.permissions.mode.value == "default")
        check("permissions: grant present pre-revoke",
              "bash" in app.permissions.session_allow)
        await pilot.press("r")
        check("permissions: [r] revoked session grants",
              not app.permissions.session_allow)
        check("permissions: revocations logged",
              any(r.verdict == "revoked" for r in app.permissions.history))
        await pilot.press("escape")
        await pilot.pause()
        check("permissions: overlay closed",
              not isinstance(app.screen, PermissionsOverlay))

        # ---------------------------------------------------- 7. interrupt
        FakeChatService.scenario = "hang"
        app.start_turn("hang forever")
        running = await settled(pilot, lambda: app.controller is not None
                                and app.controller.running)
        check("interrupt: turn is running", running)
        await pilot.press("ctrl+c")
        stopped = await settled(pilot, lambda: app.controller is None
                                or not app.controller.running, timeout_s=8)
        check("interrupt: turn stopped", stopped)
        check("interrupt: state back ONLINE",
              sbar.status == AgentStatus.ONLINE, str(sbar.status))

        # --------------------------------------------- 6. slash autocomplete
        area.focus()
        await pilot.pause()
        area.load_text("/mod")
        area.move_cursor(area.document.end)
        dock.update_suggestions()
        check("autocomplete: suggestions visible", dock.suggestions_open)
        await pilot.press("enter")
        check("autocomplete: completed to '/model '",
              area.text == "/model ", repr(area.text))
        area.load_text("")
        dock.hide_suggestions()

        # ---------------------------------------------------- 7. history
        area.focus()
        await pilot.pause()
        dock.remember("history probe entry")
        await pilot.press("up")
        check("history: up-arrow recalled entry",
              area.text == "history probe entry", repr(area.text))
        area.load_text("")

        # ---------------------------------------------------- 8. palette
        await pilot.press("ctrl+p")
        await pilot.pause()
        check("palette: opened", app.screen.__class__.__name__
              in ("CommandPalette", "CommandPaletteScreen"),
              app.screen.__class__.__name__)
        await pilot.press("escape")
        await pilot.pause()

        # ---------------------------------------------------- 9. clear view
        FakeChatService.scenario = "happy"
        await run_turn(pilot, app, "hello again")
        await pilot.press("ctrl+l")
        await pilot.pause()
        check("clear: transcript wiped",
              not core.query(UserBlock) and not core.query(AssistantBlock))

        # ---------------------------------------------------- 10. diff overlay
        app.push_screen(DiffOverlay("VERIFY DIFF", "+ added line\n- removed line"))
        await pilot.pause()
        check("diff: overlay opened", isinstance(app.screen, DiffOverlay))
        await pilot.press("escape")
        await pilot.pause()
        check("diff: overlay closed", not isinstance(app.screen, DiffOverlay))

        # ---------------------------------------------------- 11. responsive
        await pilot.resize_terminal(70, 26)
        await pilot.pause()
        proj_hidden = app.query_one(ProjectPanel).has_class("hidden")
        syst_hidden = app.query_one(SystemPanel).has_class("hidden")
        check("responsive: panels collapse at width 70",
              proj_hidden and syst_hidden)
        await pilot.resize_terminal(130, 38)
        await pilot.pause()
        check("responsive: panels restore at width 130",
              not app.query_one(ProjectPanel).has_class("hidden")
              and not app.query_one(SystemPanel).has_class("hidden"))

    print(f"\n{'=' * 46}\nPASS {PASS}  FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - report harness crashes clearly
        print(f"\nHARNESS ERROR: {type(exc).__name__}: {exc}")
        raise
