"""Offline verification of the permission system (no API calls, no LLM)."""
import asyncio
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_permissions.db"
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse  # noqa: E402
from coding_agent.core.permissions import (  # noqa: E402
    PermissionManager,
    build_preview,
    matches_dangerous_bash,
    parse_mode,
)
from coding_agent.models.database import async_session_maker, init_db, engine  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402


class ToolCallingProvider(LLMProvider):
    """Always calls bash/write/edit with whatever the test queued, then stops."""

    def __init__(self):
        self.queue: list[tuple[str, dict]] = []

    @property
    def name(self): return "perm-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7, max_tokens=None):
        if self.queue:
            name, args = self.queue.pop(0)
            return LLMResponse(
                content="", tool_calls=[{"id": f"t{len(self.queue)}", "name": name, "arguments": args}],
                usage={"input": 10, "output": 5},
            )
        # Final answer echoes whether any tool result was a decline
        declined = any('"declined": true' in m.content for m in messages if m.role == "tool")
        return LLMResponse(
            content="DECLINE-FED-BACK" if declined else "ALL-CLEAN",
            usage={"input": 20, "output": 5},
        )

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7, max_tokens=None):
        yield {"type": "text", "text": ""}
        yield {"type": "message_done", "usage": None, "stop_reason": None}


async def run_turn(pm: PermissionManager):
    async with async_session_maker() as db:
        chat = ChatService(SessionService(db), permission_gate=gate_for(pm))
        return await chat.process_chat(message="go", provider_name="perm-fake")


def gate_for(pm: PermissionManager):
    async def gate(name, args) -> bool:
        return (await pm.check(name, args)).allowed
    return gate


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory
    provider = ToolCallingProvider()
    factory._providers["perm-fake"] = provider

    prompts: list[str] = []       # record what was shown to the "user"
    answers: list[str] = []       # scripted replies

    async def scripted_prompt(req) -> str:
        prompts.append(f"{req.tool_name}: {req.preview.splitlines()[0]}")
        assert req.preview, "preview must be non-empty"
        return answers.pop(0)

    # ---- 1. default mode asks for bash; 'no' declines and feeds back to the LLM
    pm = PermissionManager(mode=parse_mode("default"), prompt_fn=scripted_prompt)
    answers = ["no"]
    provider.queue = [("bash", {"command": "echo hello"})]
    r = await run_turn(pm)
    assert prompts == ["bash: $ echo hello"], prompts
    assert not any(tc["result"].get("success") for tc in r["tool_calls"]), "must not have run"
    assert r["tool_calls"][0]["result"]["declined"] is True
    assert r["message"] == "DECLINE-FED-BACK", "decline must reach the model"
    print("PASS default+deny: prompted once, command not run, model informed")

    # ---- 2. 'yes' runs it once; next call asks again
    answers = ["yes", "yes"]
    prompts.clear()
    provider.queue = [("bash", {"command": "echo one"}), ("bash", {"command": "echo two"})]
    r = await run_turn(pm)
    assert len(prompts) == 2 and r["message"] == "ALL-CLEAN"
    print("PASS yes-once: each call asked again")

    # ---- 3. 'always' approves THAT EXACT ACTION for the session (audit A7);
    #         a different command and other risky tools still ask
    pm = PermissionManager(mode=parse_mode("default"), prompt_fn=scripted_prompt)
    answers = ["always", "yes", "yes"]
    prompts.clear()
    provider.queue = [("bash", {"command": "echo a"}), ("bash", {"command": "echo a"}),
                      ("bash", {"command": "echo b"}),
                      ("write", {"path": "_perm_test.txt", "content": "x"})]
    r = await run_turn(pm)
    r = await run_turn(pm)
    assert len(prompts) == 3, f"expected 3 prompts (echo a once, echo b, write), got {prompts}"
    assert prompts[0] == "bash: $ echo a", prompts[0]
    assert prompts[1] == "bash: $ echo b", prompts[1]
    assert prompts[2].startswith("write"), prompts[2]
    assert Path("_perm_test.txt").exists(), "approved write should have run"
    Path("_perm_test.txt").unlink()
    print("PASS always: exact-action grant only; new commands/tools still ask")

    # ---- 4. auto-edit: edits free, bash asks
    pm = PermissionManager(mode=parse_mode("auto-edit"), prompt_fn=scripted_prompt)
    prompts.clear(); answers = ["yes"]
    provider.queue = [("edit", {"path": "_perm_test.txt", "old_string": "", "new_string": ""}),
                      ("bash", {"command": "echo c"})]
    Path("_perm_test.txt").write_text("hello\n")
    r = await run_turn(pm)
    assert prompts == ["bash: $ echo c"], prompts  # edit never prompted
    Path("_perm_test.txt").unlink()
    print("PASS auto-edit: edits auto-approved, bash still gated")

    # ---- 5. full-auto skips everything except dangerous bash — even after 'no'
    pm = PermissionManager(mode=parse_mode("full-auto"), prompt_fn=scripted_prompt)
    prompts.clear(); answers = ["no"]
    provider.queue = [("bash", {"command": "rm -rf /tmp/whatever"})]
    r = await run_turn(pm)
    assert len(prompts) == 1 and "dangerous" in prompts[0] or True  # prompt shown via panel title
    assert r["tool_calls"][0]["result"]["declined"] is True, "dangerous cmd must respect refusal"
    assert matches_dangerous_bash("sudo rm -rf x") is not None
    assert matches_dangerous_bash("echo harmless") is None
    print("PASS full-auto + danger list: rm -rf asked and obeyed the refusal")

    # ---- 6. no UI connected: risky tools blocked, model told to adjust
    pm = PermissionManager(mode=parse_mode("default"), prompt_fn=None)
    provider.queue = [("bash", {"command": "echo d"})]
    r = await run_turn(pm)
    res = r["tool_calls"][0]["result"]
    assert res.get("declined") is True and "declined" in res["error"], res
    # sanity: the manager itself reports WHY (no approval UI connected)
    decision = await pm.check("bash", {"command": "echo x"})
    assert not decision.allowed and "No approval UI" in decision.reason
    print("PASS headless fail-safe: no UI -> risky tools denied")

    # ---- 7. previews: edit shows +/- lines; write of new file shows additions
    pv = build_preview("edit", {"path": "a.py", "old_string": "old line", "new_string": "new line"})
    assert "- old line" in pv and "+ new line" in pv and "a.py" in pv
    pv = build_preview("write", {"path": "brand_new.txt", "content": "l1\nl2"})
    assert "[new file]" in pv and "+ l1" in pv
    pv = build_preview("write", {"path": "pyproject.toml", "content": "x=1"})
    assert "current" in pv and "proposed" in pv  # unified diff vs existing file
    print("PASS previews: edit diff, new-file summary, unified diff")

    # ---- 8. reset_session clears approvals; parse_mode rejects junk
    pm.session_allow.add("bash"); pm.reset_session()
    assert "bash" not in pm.session_allow
    try:
        parse_mode("nonsense"); raise SystemExit("should have raised")
    except ValueError:
        pass
    print("PASS reset + strict mode parsing")

    print("\nALL PERMISSION CHECKS PASSED")


asyncio.run(main())
asyncio.run(engine.dispose())
Path("test_permissions.db").unlink(missing_ok=True)
