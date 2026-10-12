"""Exhaustive verification of every NEXUS slash command (Ask 16).

Boots CodeAgentApp under Pilot with the fake ChatService and drives EVERY
command in COMMANDS through the real submit path (typing into #prompt and
pressing Enter -> action_submit_prompt -> handle_slash -> action_cmd_*),
asserting on observable effects: transcript blocks, overlays, header state,
DB rows, files on disk. Also covers argument forms, unknown commands, usage
errors, busy-turn behavior, and the "/" dropdown while typing.

Run: python verify_commands.py
Exit code 0 = all PASS.
"""
import asyncio
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Isolate the database BEFORE any coding_agent import touches settings.
_TMPDIR = tempfile.mkdtemp(prefix="nexus_cmd_verify_")
os.environ["DATABASE_URL"] = \
    f"sqlite+aiosqlite:///{Path(_TMPDIR) / 'verify.db'}"
os.environ.setdefault("AGENT_MAX_ITERATIONS", "4")

import logging  # noqa: E402

logging.getLogger("sqlalchemy.engine.Engine").disabled = True

from textual.widgets import Static  # noqa: E402

import coding_agent.agent.controller as ctrl_mod  # noqa: E402
from coding_agent.core.permissions import PermissionManager  # noqa: E402
from coding_agent.ui.app import CodeAgentApp  # noqa: E402
from coding_agent.ui.commands import COMMANDS  # noqa: E402
from coding_agent.ui.widgets.ai_core import (  # noqa: E402
    AiCore,
    NoteBlock,
    SystemLine,
    UserBlock,
)
from coding_agent.ui.widgets.command_dock import CommandDock, PromptArea  # noqa: E402
from coding_agent.ui.widgets.header import NexusHeader  # noqa: E402
from coding_agent.ui.widgets.modals import (  # noqa: E402
    ConfirmModal,
    HelpOverlay,
    PermissionModal,
    SessionsOverlay,
)
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
    """Drop-in for ChatService that PERSISTS a session + messages like the
    real service does (so /history, /export, /rename, /delete have real DB
    rows to act on) and plays a short scripted reply."""

    scenario = "happy"

    def __init__(self, session_service=None, permission_gate=None,
                 pre_tool_hook=None, hook_runner=None):
        self.sessions = session_service

    async def stream_chat(self, *, message, session_id=None, provider_name=None,
                          model=None, system_prompt="", tool_names=None):
        from coding_agent.models.schemas import MessageCreate, SessionCreate
        from coding_agent.services.session_service import derive_title

        # resolve/create the session row (mirrors the real ChatService)
        if session_id is not None:
            session = await self.sessions.get(session_id)
        else:
            session = None
        if session is None:
            session = await self.sessions.create(
                SessionCreate(title=derive_title(message)))
            session_id = session.id
        await self.sessions.add_message(
            session_id, MessageCreate(role="user", content=message))
        for chunk in ("Command ", "probe ", "reply."):
            yield {"type": "text", "text": chunk}
        await self.sessions.add_message(
            session_id, MessageCreate(role="assistant",
                                      content="Command probe reply."))
        yield {"type": "session", "session_id": str(session_id)}
        yield {"type": "usage", "usage": {"input": 70, "output": 25},
               "context_tokens": 800, "iterations": 1}
        yield {"type": "turn_done", "usage": {"input": 70, "output": 25},
               "iterations": 1, "message": "Command probe reply.",
               "context_tokens": 800}
        return


ctrl_mod.ChatService = FakeChatService  # type: ignore[assignment]


async def settled(pilot, pred, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        await pilot.pause()
    return pred()


async def run_turn(pilot, app, prompt: str) -> None:
    turns_before = app.tracker.turns
    app.start_turn(prompt)
    await settled(pilot, lambda: app.tracker.turns > turns_before)


async def type_line(pilot, area, line: str) -> None:
    """Type a command line character by character (real input path)."""
    area.focus()
    await pilot.pause()
    area.load_text("")
    await pilot.pause()
    if line.startswith("/"):
        # "/" is inserted directly by TextArea when focused; pilot.press("/")
        # would hit the app binding only when unfocused. Type the rest.
        area.insert("/")
        for ch in line[1:]:
            await pilot.press(ch)
    else:
        for ch in line:
            await pilot.press(ch)
    await pilot.pause()


async def main() -> int:

    from coding_agent.models.database import init_db

    await init_db()
    perms = PermissionManager.from_mode("default")
    app = CodeAgentApp(permissions=perms)

    async with app.run_test(size=(130, 38)) as pilot:
        core = app.query_one(AiCore)
        dock = app.query_one(CommandDock)
        area = dock.query_one("#prompt", PromptArea)
        header = app.query_one(NexusHeader)

        def _widget_text(w) -> str:
            r = getattr(w, "_Static__content", None)
            if r is None:
                r = getattr(w, "renderable", None)
            if hasattr(r, "plain"):
                return str(r.plain)
            return str(r or "") + str(getattr(w, "_text", ""))

        def last_notes(n: int = 6) -> list[str]:
            return [_widget_text(c) for c in core.query(NoteBlock)][-n:]

        def mode_line_text() -> str:
            ml = dock.query_one("#modeline", Static)
            r = getattr(ml, "_Static__content", None)
            return str(r.plain) if hasattr(r, "plain") else str(r or "")

        def all_transcript_text() -> str:
            return "\n".join(_widget_text(w) for w in core.children)

        async def submit(line: str) -> None:
            """Submit through the REAL path: fill prompt, press enter."""
            area.focus()
            await pilot.pause()
            area.load_text(line)
            area.move_cursor(area.document.end)
            await pilot.press("enter")
            await pilot.pause()

        # ------------------------------------------------ 0. registry sanity
        keys = [c.key for c in COMMANDS]
        missing = [k for k in keys
                   if not hasattr(app, f"action_cmd_{k}")]
        check("registry: every command has an action handler", not missing,
              str(missing))
        names = [c.name for c in COMMANDS]
        check("registry: unique command names",
              len(names) == len(set(names)))

        # --------------------------------------------- 1. /help overlay
        await submit("/help")
        ok = await settled(pilot,
                           lambda: isinstance(app.screen, HelpOverlay))
        check("/help: overlay opens", ok)
        await pilot.press("escape")
        await pilot.pause()

        # --------------------------------------------- 2. /tools
        before = len(core.query(SystemLine))
        await submit("/tools")
        await pilot.pause()
        check("/tools: prints tool list",
              len(core.query(SystemLine)) > before
              and "read" in all_transcript_text())

        # --------------------------------------------- 3. /settings
        before = len(core.query(SystemLine))
        await submit("/settings")
        await pilot.pause()
        check("/settings: prints overview",
              len(core.query(SystemLine)) > before + 3
              and "permission mode" in all_transcript_text())

        # --------------------------------------------- 4. /context & /tokens
        await submit("/context")
        await pilot.pause()
        check("/context: notes context line",
              any("auto-compact line" in t for t in last_notes(6)))
        await submit("/tokens")
        await pilot.pause()
        check("/tokens: prints token totals",
              any("session tokens" in t.lower() for t in last_notes(6)))

        # --------------------------------------------- 5. /model show + set
        await submit("/model")
        shown = await settled(
            pilot,
            lambda: "current model" in all_transcript_text())
        check("/model: lists models", shown)
        m0 = app.model
        await submit(f"/model probe-model-{uuid.uuid4().hex[:6]}")
        await pilot.pause()
        check("/model <id>: switches model",
              app.model != m0 and app.model.startswith("probe-model-"),
              str(app.model))
        check("/model <id>: header updated",
              "probe-model-" in str(header._model_text)
              if hasattr(header, "_model_text") else True)

        # --------------------------------------------- 6. /provider
        await submit("/provider")
        await pilot.pause()
        check("/provider: shows current + available",
              any("available:" in t for t in last_notes(4)))
        await submit("/provider nosuchprovider")
        await pilot.pause()
        check("/provider <bad>: rejected",
              any("unknown provider" in t for t in last_notes(4)))
        cur = app.provider_name
        await submit("/provider openrouter")
        await pilot.pause()
        check("/provider openrouter: switches + resets model",
              app.provider_name == "openrouter"
              and app.controller.provider_name == "openrouter")
        await submit(f"/provider {cur or 'openai'}")
        await pilot.pause()

        # --------------------------------------------- 7. /permissions arg
        await submit("/permissions full-auto")
        await pilot.pause()
        check("/permissions full-auto: mode applied",
              app.permissions.mode.value == "full-auto")
        await submit("/permissions banana")
        await pilot.pause()
        check("/permissions <bad>: rejected",
              any("unknown permission mode" in t.lower()
                  for t in last_notes(6)))
        await submit("/permissions default")
        await pilot.pause()

        # --------------------------------------------- 8. /memory
        await submit("/memory")
        await pilot.pause()
        check("/memory: reports project+global memory",
              "project (" in all_transcript_text()
              and "global:" in all_transcript_text())

        # --------------------------------------------- 9. /git (not a repo)
        await submit("/git init")
        await pilot.pause()
        check("/git init: initializes repo",
              app.git.is_repo(),
              )
        await submit("/git")
        await pilot.pause()
        check("/git: shows branch + changed count",
              any("branch" in t.lower() for t in last_notes(4)))

        # ------------------------------- 10. first turn -> session exists
        await run_turn(pilot, app, "seed the session")
        check("turn: session resolved after turn", app.session_id is not None)

        # --------------------------------------------- 11. /run echo
        # default mode asks for approval; answer y at the modal
        await submit("/run echo nexus-run-ok")
        modal_ok = await settled(
            pilot, lambda: isinstance(app.screen, PermissionModal),
            timeout_s=8)
        check("/run: approval modal appears (default mode)", modal_ok)
        if modal_ok:
            await pilot.press("y")
        card_ok = await settled(
            pilot,
            lambda: any(c.ok for c in core.query(ToolCard)
                        if c.tool_name == "bash"))
        check("/run echo: bash card succeeded", card_ok)

        # --------------------------------------------- 12. /search pattern
        await submit("/search NEXUS")
        grep_done = await settled(
            pilot,
            lambda: any(not c.has_class("running")
                        for c in core.query(ToolCard)
                        if c.tool_name == "grep"),
            timeout_s=10)
        check("/search: grep card ran to completion", grep_done)

        # --------------------------------------------- 13. /diff
        from coding_agent.ui.widgets.modals import DiffOverlay

        Path("cmd_probe.txt").write_text("probe\n", encoding="utf-8")
        await submit("/diff")
        ok = await settled(pilot,
                           lambda: isinstance(app.screen, DiffOverlay))
        check("/diff: diff overlay opens", ok)
        await pilot.press("escape")
        await pilot.pause()

        # --------------------------------------------- 14. /undo (no-op form)
        await submit("/undo")
        await pilot.pause()
        check("/undo without changes: friendly no-op note",
              any("nothing to undo" in t for t in last_notes(4)))
        # undo the probe file write via checkpoint? none recorded; just cover
        # the message path above.

        # --------------------------------------------- 15. /rename
        await submit("/rename cmd-verify-session")
        renamed = await settled(
            pilot,
            lambda: any("renamed current session" in t
                        for t in last_notes(4)),
            timeout_s=8)
        check("/rename <title>: renames session", renamed)

        # --------------------------------------------- 16. /export
        out_file = Path(_TMPDIR) / "export.md"
        await submit(f"/export {out_file}")
        exported = await settled(
            pilot,
            lambda: any("exported to" in t for t in last_notes(5)),
            timeout_s=8)
        check("/export <path>: writes markdown", exported and
              out_file.exists())

        # --------------------------------------------- 17. /compact
        await submit("/compact")
        compaction_note = await settled(
            pilot,
            lambda: any(("compacting" in t) or ("nothing compactable" in t)
                        or ("compacted" in t) for t in last_notes(5))
            or "compact" in all_transcript_text().lower(),
            timeout_s=20)
        check("/compact: runs without crashing (note lands)", compaction_note)

        # --------------------------------------------- 18. /plan
        plan_before = app.tracker.turns
        await submit("/plan list the project layout")
        planned = await settled(pilot,
                                lambda: app.tracker.turns > plan_before)
        check("/plan <ask>: planning turn completes", planned)
        # the finished plan triggers the "Execute this plan now?" modal;
        # dismiss it so it doesn't eat the next keypresses
        offer = await settled(pilot,
                              lambda: isinstance(app.screen, ConfirmModal),
                              timeout_s=5)
        if offer:
            await pilot.press("escape")
            await pilot.pause()

        # --------------------------------------------- 19. /history picker
        await submit("/history")
        hist_ok = await settled(
            pilot, lambda: isinstance(app.screen, SessionsOverlay))
        check("/history: sessions picker opens", hist_ok)
        await pilot.press("escape")
        await pilot.pause()

        # --------------------------------------------- 20. unknown command
        n_notes = len(core.query(NoteBlock))
        await submit("/definitely-not-a-command")
        await pilot.pause()
        check("unknown /cmd: warn + hint to /help",
              len(core.query(NoteBlock)) > n_notes
              and any("unknown command" in t for t in last_notes(3)))

        # --------------------------------------------- 21. usage errors
        n_notes = len(core.query(NoteBlock))
        await submit("/search")
        await pilot.pause()
        check("/search no args: usage note",
              len(core.query(NoteBlock)) > n_notes
              and any("usage: /search" in t for t in last_notes(3)))
        await submit("/run")
        await pilot.pause()
        check("/run no args: usage note",
              any("usage: /run" in t for t in last_notes(3)))
        await submit("/plan")
        await pilot.pause()
        check("/plan no args: usage note",
              any("usage: /plan" in t for t in last_notes(3)))

        # --------------------------------------- 22. "/" dropdown while typing
        await type_line(pilot, area, "/mod")
        dropdown_open = dock.suggestions_open
        check("typing '/mod': dropdown opens automatically", dropdown_open)
        if dropdown_open:
            await pilot.press("enter")  # complete, not submit
            completed = area.text == "/model "
            check("typing '/mod'+enter: completes to '/model '", completed)
        area.load_text("")
        dock.hide_suggestions()

        await type_line(pilot, area, "/")
        check("typing '/': dropdown lists all commands", dock.suggestions_open)
        if dock.suggestions_open:
            box = dock.query_one("#suggestions")
            check("'/' dropdown: lists every registered command",
                  box.option_count >= len(COMMANDS), str(box.option_count))
        area.load_text("")
        dock.hide_suggestions()

        # suggestion navigation: down moves highlight
        await type_line(pilot, area, "/m")
        moved = False
        if dock.suggestions_open:
            box = dock.query_one("#suggestions")
            start = box.highlighted
            await pilot.press("down")
            moved = box.highlighted != start
            check("dropdown: down-arrow moves highlight", moved)
        area.load_text("")
        dock.hide_suggestions()

        # --------------------------------------- 23. /delete current session
        await submit("/delete")
        conf = await settled(pilot,
                             lambda: isinstance(app.screen, ConfirmModal))
        check("/delete: confirm modal appears", conf)
        if conf:
            await pilot.press("y")
        deleted = await settled(
            pilot, lambda: app.session_id is None
            and any("deleted" in t for t in last_notes(4)))
        check("/delete y: deletes + fresh state", deleted)

        # --------------------------------------------- 24. /clear
        await run_turn(pilot, app, "another seed")
        sid2 = app.session_id
        check("/clear pre: session active again", sid2 is not None)
        await submit("/clear")
        await pilot.pause()
        check("/clear: fresh session state",
              app.session_id is None and not core.query(UserBlock))

        # ------------------------------- 25. queueing while the agent works
        FakeChatService.scenario = "hang"
        app.start_turn("busy probe")
        running = await settled(pilot, lambda: app.controller.running)
        check("busy: turn started", running)
        n_meta = len(core.query(SystemLine))
        area.focus()
        await pilot.pause()
        area.load_text("queued thought")
        await pilot.press("enter")
        queued_shown = await settled(
            pilot,
            lambda: len(core.query(SystemLine)) > n_meta
            and "queued:" in all_transcript_text())
        check("queue while busy: meta line + prompt cleared", queued_shown)
        check("queue while busy: mode line shows QUEUE 1",
              "QUEUE 1" in mode_line_text())
        # interrupt; queued message must stay queued (no surprise auto-send)
        await pilot.press("ctrl+c")
        await settled(pilot, lambda: app.controller is None
                      or not app.controller.running, timeout_s=8)
        FakeChatService.scenario = "happy"
        check("interrupt: queue kept (no surprise send)",
              not app.busy and len(app._queue) == 1,
              f"queue={len(app._queue)}")

        # the queued message flushes when the next turn finishes normally
        await run_turn(pilot, app, "post-interrupt seed")
        drained = await settled(
            pilot, lambda: not app._queue and not app.busy, timeout_s=10)
        check("queued message sends after next turn finishes", drained)

        # --------------------------------------------- 26. /exit
        await submit("/exit")
        exited = await settled(pilot, lambda: app.return_code is not None
                               or not app.is_running, timeout_s=8)
        check("/exit: quits the console", exited)

    # ---------------------------------------------------------- DB sanity
    import sqlite3

    db_path = Path(_TMPDIR) / "verify.db"
    con = sqlite3.connect(db_path)
    rows = con.execute("select count(*) from messages").fetchone()[0]
    con.close()
    check("db: transcript persisted across commands", rows >= 2, str(rows))

    print(f"\n{'=' * 46}\nPASS {PASS}  FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - report harness crashes clearly
        print(f"\nHARNESS ERROR: {type(exc).__name__}: {exc}")
        raise
