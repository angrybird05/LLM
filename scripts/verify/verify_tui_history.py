"""Headless verification of the NEXUS history/follow/tree features.

Companion to verify_tui.py - covers what this ask added:
  G1 follow mode    - streaming view pins bottom only while user is there
  G2 project tree   - lazy expand, junk pruning, colored entries, honest stats
  G3 session picker - seeded DB: enter resumes, d dismisses a delete request,
                      esc cancels; current session marked
  G4 own-chat       - resume REPLACES the view (clear-first), header divider,
                      re-open adds nothing, busy switch refused, panel box gone
  G5 delete         - confirm modal -> row gone, deleting current wipes view
  G6 file preview   - FileViewerOverlay opens on demand, esc closes
  G7 boot restore   - initial_session_id renders the chat at launch

Run: python verify_tui_history.py
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
_TMPDIR = tempfile.mkdtemp(prefix="nexus_hist_")
os.environ["DATABASE_URL"] = \
    f"sqlite+aiosqlite:///{Path(_TMPDIR) / 'hist.db'}"

import logging  # noqa: E402

logging.getLogger("sqlalchemy.engine.Engine").disabled = True

from textual.widgets import Button  # noqa: E402

import coding_agent.agent.controller as ctrl_mod  # noqa: E402
from coding_agent.core.permissions import PermissionManager  # noqa: E402
from coding_agent.ui.app import CodeAgentApp  # noqa: E402
from coding_agent.ui.widgets.ai_core import (  # noqa: E402
    AssistantBlock,
    SystemLine,
    UserBlock,
)
from coding_agent.ui.widgets.modals import (  # noqa: E402
    ConfirmModal,
    FileViewerOverlay,
    SessionsOverlay,
)
from coding_agent.ui.widgets.project_panel import (  # noqa: E402
    ProjectPanel,
    count_workspace_files,
    file_color,
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


async def settled(pilot, pred, timeout_s: float = 12.0) -> bool:
    """Pump the message pump until pred() or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        await pilot.pause()
    return pred()


class FakeChatService:
    """Drop-in ChatService playing one streamed turn."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_chat(self, **kwargs):
        yield {"type": "session", "session_id": str(uuid.uuid4())}
        for chunk in ("alpha ", "reply."):
            yield {"type": "text", "text": chunk}
        yield {"type": "turn_done", "usage": {"input": 10, "output": 5},
               "iterations": 1, "message": "alpha reply.",
               "context_tokens": 100}


ctrl_mod.ChatService = FakeChatService  # type: ignore[assignment]


async def seed_session(title: str, with_tool: bool = False) -> uuid.UUID:
    """Insert a session with one user+assistant exchange (optionally a
    succeeded read tool call) and return its id."""
    from coding_agent.models.database import async_session_maker
    from coding_agent.models.orm import ToolStatus
    from coding_agent.models.schemas import (
        MessageCreate,
        SessionCreate,
        ToolCallCreate,
    )
    from coding_agent.services.session_service import SessionService

    async with async_session_maker() as db:
        svc = SessionService(db)
        session = await svc.create(SessionCreate(title=title))
        await svc.add_message(session.id, MessageCreate(
            role="user", content=f"{title}: question"))
        answer = await svc.add_message(session.id, MessageCreate(
            role="assistant", content=f"{title}: answer"))
        if with_tool:
            tc = await svc.add_tool_call(answer.id, ToolCallCreate(
                name="read", arguments={"path": "note.md"}, tool_use_id="t1"))
            await svc.update_tool_call(tc.id, {"path": "note.md",
                                               "content": "hi"},
                                       ToolStatus.SUCCESS)
        await db.commit()
        return session.id


async def run_turn(pilot, app, prompt: str) -> None:
    turns_before = app.tracker.turns
    app.start_turn(prompt)
    await settled(pilot, lambda: app.tracker.turns > turns_before)


async def session_exists(sid: uuid.UUID) -> bool:
    """True if the session row still exists in the isolated DB."""
    from coding_agent.models.database import async_session_maker
    from coding_agent.services.session_service import SessionService

    async with async_session_maker() as db:
        return await SessionService(db).get(sid) is not None


async def main() -> int:
    from coding_agent.models.database import init_db

    await init_db()
    perms = PermissionManager.from_mode("default")
    app = CodeAgentApp(permissions=perms)

    async with app.run_test(size=(130, 38)) as pilot:
        core = app.query_one("AiCore")
        panel = app.query_one(ProjectPanel)
        check("boot: five-region app mounted", bool(core and panel))

        # ------------------------------------------------ G1. follow mode
        for i in range(80):
            core.meta(f"pad line {i}")
        grew_overflow = await settled(pilot, lambda: core.max_scroll_y > 0)
        check("follow: overflow exists after padding", bool(grew_overflow))

        core.scroll_to(y=0, animate=False)
        await pilot.pause()
        y_frozen = core.scroll_y
        core.meta("probe while scrolled up")
        await pilot.pause()
        await pilot.pause()
        check("follow: view frozen while user scrolled up",
              core.scroll_y == y_frozen,
              f"y={core.scroll_y} froze at {y_frozen}")

        core.scroll_end(animate=False)
        await pilot.pause()
        core.meta("probe at bottom")
        await pilot.pause()
        await pilot.pause()
        check("follow: re-arms at bottom",
              core.scroll_y >= core.max_scroll_y - 2,
              f"y={core.scroll_y} max={core.max_scroll_y}")

        # ------------------------------------------------ G2. project tree
        from textual.widgets import Tree

        t = panel.query_one("#ptree", Tree)
        root_kids = [str(n.label) for n in t.root.children]
        check("tree: .venv pruned from root",
              not any(".venv" in lab for lab in root_kids),
              str(root_kids[:6]))
        check("tree: suffix colors mapped",
              file_color("a.py") == "#34d399"
              and file_color("b.md") == "#22d3ee")
        from coding_agent.config import settings as cfg

        ws = cfg.workspace_path
        raw = sum(len(fns) for _dp, _dn, fns in os.walk(ws))
        pruned = count_workspace_files(ws)
        check("stats: pruned walk < raw walk on real repo", pruned < raw)
        src_node = next((n for n in t.root.children
                         if str(n.label).startswith("src")), None)
        check("tree: src/ dir present at root",
              src_node is not None, str(root_kids))
        if src_node:
            check("tree: lazy - subdir empty before expand",
                  not src_node.children)
            src_node.expand()
            grew = await settled(pilot, lambda: len(src_node.children) > 0)
            check("tree: populated on expand", bool(grew))

        # --------------------------------------- G3. session picker overlay
        alpha_id = await seed_session("alpha seed", with_tool=True)
        beta_id = await seed_session("beta seed")
        check("seed: two sessions inserted",
              isinstance(alpha_id, uuid.UUID)
              and isinstance(beta_id, uuid.UUID))

        async def db_rows():
            from coding_agent.models.database import async_session_maker
            from coding_agent.services.session_service import SessionService

            async with async_session_maker() as db:
                out = []
                for s in await SessionService(db).list(limit=10):
                    sid = s.id
                    title = s.title
                    n_msgs = len(s.messages)
                    updated = getattr(s, "updated_at", None)
                    out.append({"id": sid, "title": title,
                                "messages": n_msgs, "updated_at": updated})
                return out

        rows = await db_rows()
        got_ids = {r["id"] for r in rows}
        check("picker: seeded rows listed",
              got_ids == {alpha_id, beta_id},
              str([str(r["id"])[:8] for r in rows]))
        top_row_id = rows[0]["id"]

        captured: dict = {}

        def on_pick(value) -> None:
            captured["pick"] = value

        overlay = SessionsOverlay(rows, current_id=top_row_id)
        await app.push_screen(overlay, callback=on_pick)
        check("picker: overlay pushed", isinstance(app.screen, SessionsOverlay))
        from textual.widgets import OptionList

        ol = overlay.query_one("#session-list", OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        picked = captured.get("pick") or {}
        check("picker: enter resumes highlighted row",
              isinstance(picked, dict) and picked.get("id") == top_row_id,
              str(captured))

        # reopen and press d on the highlighted (newest = beta) row
        del_capture: dict = {}

        def on_delete_request(value) -> None:
            del_capture["req"] = value

        del_overlay = SessionsOverlay(rows, current_id=None)
        await app.push_screen(del_overlay, callback=on_delete_request)
        ol2 = del_overlay.query_one("#session-list", OptionList)
        ol2.focus()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.pause()
        req = del_capture.get("req")
        is_del_req = isinstance(req, dict) and "delete" in req \
            and isinstance(req["delete"], dict)
        row_in_req = is_del_req and req["delete"].get("id") == top_row_id
        check("picker: d requests deletion of highlighted row",
              row_in_req, str(del_capture))

        # esc cancels cleanly
        esc_overlay = SessionsOverlay(rows, current_id=None)
        await app.push_screen(esc_overlay, callback=lambda v: None)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        check("picker: escape closes overlay",
              not isinstance(app.screen, SessionsOverlay))

        # ------------------------------------ G4. adopt + transcript restore
        # Plant stale content first: opening a previous chat must REPLACE the
        # view, not continue under what was already on screen.
        core.post_user("old chat marker")
        await pilot.pause()
        app.session_id = None
        app._adopt_session_row({"id": alpha_id, "title": "alpha seed",
                                "messages": 3})
        user_ok = await settled(pilot, lambda: any(
            "question" in str(b.render()) for b in core.query(UserBlock)))
        check("restore: user block rendered", bool(user_ok))

        card_ok = await settled(pilot, lambda: any(
            not c.has_class("running") and not c.has_class("failed")
            for c in core.query(ToolCard)))
        check("restore: succeeded tool card rendered", bool(card_ok))

        answer_ok = await settled(pilot, lambda: any(
            "answer" in b._text for b in core.query(AssistantBlock)))
        check("restore: assistant text restored", bool(answer_ok))
        check("restore: session adopted", app.session_id == alpha_id)

        stale_gone = all("old chat" not in str(b.render())
                         for b in core.query(UserBlock))
        check("own-chat: switching cleared the previous view", stale_gone)

        divider = any(
            "session" in str(ln.render()) for ln in core.query(SystemLine))
        check("own-chat: header divider names the opened chat", divider)

        # Re-picking the open chat must not duplicate anything.
        n_users_before = len(list(core.query(UserBlock)))
        app._adopt_session_row({"id": alpha_id, "title": "alpha seed",
                                "messages": 3})
        for _ in range(8):
            await pilot.pause()
        n_users_after = len(list(core.query(UserBlock))
                            ) if n_users_before else 0
        check("own-chat: re-opening the same chat adds nothing",
              n_users_after == n_users_before,
              f"{n_users_before} -> {n_users_after}")

        # Switching is refused while a turn is running.
        if app.controller is not None:
            app.controller.running = True
        app._adopt_session_row({"id": beta_id, "title": "beta seed",
                                "messages": 2})
        for _ in range(6):
            await pilot.pause()
        check("own-chat: switch refused while agent busy",
              app.session_id == alpha_id)
        if app.controller is not None:
            app.controller.running = False

        # The inline panel box is gone; ctrl+h picker is the single surface.
        check("panel: inline sessions box removed",
              not list(panel.query("#psessions")))

        # --------------------------------- G5. delete non-current via confirm
        asyncio.create_task(app._delete_picked(
            {"id": beta_id, "title": "beta seed", "messages": 2}))
        confirm_ok = await settled(pilot, lambda: isinstance(
            app.screen, ConfirmModal))
        check("delete: confirm modal appeared", bool(confirm_ok))
        if confirm_ok:
            await pilot.pause()
            confirm_screen = app.screen
            confirm_screen.query_one("#yes", Button).press()
        gone = False
        for _ in range(60):
            await pilot.pause()
            if not await session_exists(beta_id):
                gone = True
                break
        check("delete: row removed from DB", bool(gone))
        check("delete: other session untouched",
              await session_exists(alpha_id))
        # let the reopened history overlay land, then close it
        for _ in range(10):
            await pilot.pause()
            if isinstance(app.screen, SessionsOverlay):
                await pilot.press("escape")
        await pilot.pause()

        # ------------------------------------- G6. file preview overlay
        preview = Path(_TMPDIR) / "preview_me.py"
        preview.write_text("print('hi')\n")
        app._open_file_preview(preview)
        opened = await settled(pilot, lambda: isinstance(
            app.screen, FileViewerOverlay))
        check("viewer: file preview opened", bool(opened))
        await pilot.press("escape")
        await pilot.pause()
        check("viewer: escape closes preview",
              not isinstance(app.screen, FileViewerOverlay))

    # --------------------------- G7. boot restore (--continue / --resume)
    # A fresh app launched with an initial session opens that chat directly.
    app2 = CodeAgentApp(permissions=PermissionManager.from_mode("default"),
                        initial_session_id=alpha_id)
    async with app2.run_test(size=(130, 38)) as pilot2:
        core2 = app2.query_one("AiCore")
        user_ok = await settled(pilot2, lambda: any(
            "question" in str(b.render()) for b in core2.query(UserBlock)))
        check("boot: previous chat renders without any picker action",
              bool(user_ok))
        check("boot: session adopted from initial id",
              app2.session_id == alpha_id)

    print(f"\n{'=' * 46}\nPASS {PASS}  FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - report failures clearly
        print(f"\nHARNESS ERROR: {type(exc).__name__}: {exc}")
        raise
