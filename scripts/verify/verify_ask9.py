"""Offline verification of Ask 9: plan mode pieces, subagents, hooks,
custom commands (no API calls)."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

WS = Path(tempfile.mkdtemp(prefix="ask9_ws_"))
os.environ["WORKSPACE_ROOT"] = str(WS)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_ask9.db"

sys.path.insert(0, str(Path(__file__).parent / "src"))

# --- workspace fixtures: hooks config + a custom slash command -----------------
PY = str(Path(sys.executable))
(WS / ".agent").mkdir()
(WS / ".agent" / "settings.json").write_text(json.dumps({
    "hooks": {
        "pre_tool": [{"tools": ["write"],
                      "run": f'"{PY}" -c "import sys; sys.exit(2)"'}],
        "post_tool": [{"tools": "*",
                       "run": (f'"{PY}" -c "import os,pathlib; '
                               f"pathlib.Path(os.environ[\'HOOK_MARKER\'])"
                               f".write_text(os.environ['TOOL_NAME'] + '|'"
                               f" + os.environ.get('TOOL_STATUS', ''))\"")}],
    }
}), encoding="utf-8")
(WS / ".agent" / "commands").mkdir()
(WS / ".agent" / "commands" / "deploy.md").write_text(
    "Deploy $ARGS to the staging environment right now.", encoding="utf-8")

from coding_agent.config import settings  # noqa: E402

assert Path(WS).resolve() == settings.workspace_path

from coding_agent.core.hooks import HookRunner  # noqa: E402
from coding_agent.core.llm.base import LLMProvider, LLMResponse  # noqa: E402
from coding_agent.core.tools.base import READ_ONLY_TOOL_NAMES, registry  # noqa: E402
from coding_agent.core.tools.task import SUBAGENT_SYSTEM, SubagentTool  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402
from coding_agent.tui.commands import (  # noqa: E402
    custom_command_hint,
    load_custom_commands,
    render_custom_command,
)

MARKER = WS / "hook_marker.txt"


class FakeProvider(LLMProvider):
    """Parent turns come from parent_queue ('None' -> plain text). Calls whose
    first message is the subagent system prompt take from child_queue instead."""

    def __init__(self):
        self.parent_queue: list = []
        self.child_queue: list = []
        self.child_tool_names = None
        self.child_histories: list[list] = []

    @property
    def name(self): return "ask9-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7,
                       max_tokens=None):
        if messages and messages[0].role == "system" \
                and messages[0].content.startswith(SUBAGENT_SYSTEM[:30]):
            self.child_tool_names = [d.name for d in (tools or [])]
            self.child_histories.append(list(messages))
            if self.child_queue:
                item = self.child_queue.pop(0)
                if item is not None:
                    return LLMResponse(content="", tool_calls=[item],
                                       usage={"input": 5, "output": 5})
            return LLMResponse(content="CHILD-REPORT", usage={"input": 5, "output": 5})
        if self.parent_queue:
            item = self.parent_queue.pop(0)
            if item is not None:
                return LLMResponse(content="", tool_calls=[item],
                                   usage={"input": 10, "output": 5})
        return LLMResponse(content="done", usage={"input": 10, "output": 5})

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7,
                              max_tokens=None):
        yield {"type": "text", "text": "done"}
        yield {"type": "message_done", "usage": {"input": 7, "output": 3},
               "stop_reason": "end_turn"}


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory

    provider = FakeProvider()
    factory._providers["ask9-fake"] = provider

    # ---- 1. hook config loading (present + absent)
    runner = HookRunner.load(WS)
    assert len(runner.pre_rules) == 1 and runner.pre_rules[0].matches("write")
    assert not runner.pre_rules[0].matches("read")
    assert len(runner.post_rules) == 1 and runner.post_rules[0].tools is None
    empty = HookRunner.load(WS / ".agent" / ".." / "nowhere-fine")
    assert empty.pre_rules == [] and empty.post_rules == []
    print("PASS HookRunner loads .agent/settings.json; missing config = no-op")

    # ---- 2. pre-hook veto blocks the tool before anything happens
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc, hook_runner=runner)
        provider.parent_queue = [
            {"id": "t1", "name": "write",
             "arguments": {"path": "hello.txt", "content": "nope"}}]
        res = await chat.process_chat(message="try to write hello.txt",
                                      session_id=None, provider_name="ask9-fake")
        blocked = res["tool_calls"][0]["result"]
        assert blocked.get("blocked_by_hook") and "pre-tool hook" in blocked["error"]
        assert not (WS / "hello.txt").exists(), "vetoed write must not touch disk"
        assert not MARKER.exists(), "vetoed call must skip post hooks too"
    print("PASS failing pre-tool hook vetoes the call; post hooks skipped")

    # ---- 3. successful call runs post hooks with TOOL_NAME / TOOL_STATUS
    os.environ["HOOK_MARKER"] = str(MARKER)
    post_only = HookRunner(post_rules=runner.post_rules)
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc, hook_runner=post_only)
        provider.parent_queue = [
            {"id": "t2", "name": "write",
             "arguments": {"path": "hello.txt", "content": "hi"}}]
        await chat.process_chat(message="write hello.txt", session_id=None,
                                provider_name="ask9-fake")
        assert (WS / "hello.txt").exists()
        assert MARKER.read_text() == "write|ok", MARKER.read_text()
    print(f"PASS post-tool hook fired with env ({MARKER.read_text()!r})")

    # ---- 4. custom slash commands
    cmds = load_custom_commands(WS)
    assert "deploy" in cmds
    assert render_custom_command(cmds["deploy"], "api v2") == \
        "Deploy api v2 to the staging environment right now."
    hint = custom_command_hint(cmds)
    assert "/deploy" in hint
    assert custom_command_hint({}) == ""
    print("PASS custom commands load, $ARGS substitution, help hint")

    # ---- 5. task subagent: restricted tools + full loop through ChatService
    assert "task" not in READ_ONLY_TOOL_NAMES
    assert {"write", "edit", "bash"}.isdisjoint(READ_ONLY_TOOL_NAMES)

    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        provider.parent_queue = [
            {"id": "t3", "name": "task",
             "arguments": {"prompt": "find every TODO comment and report"}}]
        provider.child_queue = []
        res = await chat.process_chat(message="delegate the TODO sweep",
                                      session_id=None, provider_name="ask9-fake")
        out = res["tool_calls"][0]["result"]
        assert out["report"] == "CHILD-REPORT", out
        assert out["iterations"] == 1
        assert provider.child_tool_names is not None
        assert set(provider.child_tool_names) <= READ_ONLY_TOOL_NAMES, \
            provider.child_tool_names
        assert "task" not in provider.child_tool_names
        assert "write" not in provider.child_tool_names
    print(f"PASS task tool ran a child loop; child saw only "
          f"{sorted(provider.child_tool_names)}")

    # ---- 6. subagent guardrails directly: gate denial + non-read-only refusal
    task_tool = SubagentTool()
    deny_gate = lambda name, arguments: asyncio.sleep(0, False)  # noqa: E731

    provider.child_queue = [{"id": "c1", "name": "read",
                             "arguments": {"path": "hello.txt"}}]
    ctx = {"provider": provider, "permission_gate": deny_gate}
    out = await task_tool.execute({"prompt": "read hello.txt"}, dict(ctx))
    assert out["report"] == "CHILD-REPORT"
    tool_msg = next(m.content for m in provider.child_histories[-1]
                    if m.role == "tool")
    assert '"declined": true' in json.dumps(json.loads(tool_msg)), tool_msg

    provider.child_queue = [{"id": "c2", "name": "bash",
                             "arguments": {"command": "rm something"}}]
    ctx2 = {"provider": provider, "permission_gate": None}
    await task_tool.execute({"prompt": "run bash"}, dict(ctx2))
    tool_msg2 = next(m.content for m in provider.child_histories[-1]
                     if m.role == "tool")
    assert "read-only tools" in json.loads(tool_msg2)["error"]

    missing = await task_tool.execute({}, {"provider": provider})
    assert "prompt" in missing["error"]
    print("PASS subagent respects the permission gate and refuses mutating tools")

    # ---- 7. root-alias paths resolve to the workspace root, escapes still die
    from coding_agent.core.tools.paths import resolve_in_workspace

    for alias in ("/", "\\", ".", "./", ".\\", "  ", ""):
        assert resolve_in_workspace(alias) == settings.workspace_path, alias
    outside = Path(settings.workspace_path.anchor) / "elsewhere"
    try:
        resolve_in_workspace(str(outside))
        raise SystemExit("escape path must be rejected")
    except ValueError:
        pass
    lst = registry.get("list")
    listed = await lst.execute({"path": "/"}, {"workspace_root": str(WS)})
    assert any(i["name"] == ".agent" for i in listed["items"])
    print("PASS list '/' now lists the workspace root; real escapes still rejected")

    print("\nALL ASK 9 CHECKS PASSED")


try:
    asyncio.run(main())
finally:
    asyncio.run(engine.dispose())
    shutil.rmtree(WS, ignore_errors=True)
    Path("test_ask9.db").unlink(missing_ok=True)
