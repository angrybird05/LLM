"""Offline verification of Ask 8: git integration & checkpoints (no API calls)."""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

WS = Path(tempfile.mkdtemp(prefix="ask8_ws_"))
os.environ["WORKSPACE_ROOT"] = str(WS)  # read at settings import time
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_git.db"
# git identity for the test commits
os.environ["GIT_AUTHOR_NAME"] = os.environ["GIT_COMMITTER_NAME"] = "verify"
os.environ["GIT_AUTHOR_EMAIL"] = os.environ["GIT_COMMITTER_EMAIL"] = "verify@test"

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.config import settings  # noqa: E402

assert Path(WS).resolve() == settings.workspace_path, \
    "workspace must point at the temp dir"

from coding_agent.core.llm.base import LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.git_service import (  # noqa: E402
    CheckpointStore,
    GitService,
    generate_commit_message,
    make_snapshot_hook,
)
from coding_agent.services.session_service import SessionService  # noqa: E402


class ScriptedProvider(LLMProvider):
    def __init__(self, queue=None, message="done"):
        self.queue = list(queue or [])
        self.message = message

    @property
    def name(self): return "git-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7,
                       max_tokens=None):
        if tools is None or not tools:      # commit-message generation
            return LLMResponse(content="feat: add note generator",
                               usage={"input": 5, "output": 5})
        if self.queue:
            item = self.queue.pop(0)
            return LLMResponse(
                content="", tool_calls=[item],
                usage={"input": 10, "output": 5},
            )
        return LLMResponse(content=self.message, usage={"input": 10, "output": 5})

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7,
                              max_tokens=None):
        yield {"type": "text", "text": self.message}
        yield {"type": "message_done", "usage": {"input": 7, "output": 3},
               "stop_reason": "end_turn"}


class BoomProvider(LLMProvider):
    """Raises on every call — exercises the commit-message fallback."""

    @property
    def name(self): return "boom"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7,
                       max_tokens=None):
        raise RuntimeError("api down")

    async def stream_complete(self, messages, **kwargs):
        raise RuntimeError("api down")


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory

    provider = ScriptedProvider()
    factory._providers["git-fake"] = provider

    git = GitService()

    # ---- 1. detection + init
    assert git.is_repo() is False, "fresh tmp dir must not be a repo"
    git.init()
    assert git.is_repo() and git.has_commits() is False

    (WS / ".gitignore").write_text("*.log\n")
    (WS / "base.txt").write_text("base\n")
    git.stage([".gitignore", "base.txt"])
    sha = git.commit("init: workspace baseline")
    assert len(sha) == 8 and git.has_commits()
    print(f"PASS repo detection, /git init path, baseline commit {sha}")

    # ---- 2. checkpoints: snapshot hook + undo of modify & create
    store = CheckpointStore()
    hook = make_snapshot_hook(store)

    target = WS / "base.txt"
    await hook("write", {"path": "base.txt", "content": "changed"})
    target.write_text("changed\n")           # the actual change happens after the hook
    rec = store.undo_last()
    assert rec is not None and target.read_text() == "base\n"
    assert store.records == []

    created = WS / "sub" / "made_by_agent.txt"
    await hook("edit", {"path": str(created)})   # absolute path also allowed in-workspace
    assert not created.exists()                  # recorded as non-existent...
    created.parent.mkdir()
    created.write_text("agent made me")          # ...then agent creates it
    rec = store.undo_last()
    assert rec.before is None and not created.exists(), "undo must delete agent-created file"
    print("PASS checkpoint hook records pre-state; undo restores modifies, deletes creates")

    # LIFO across several changes to the same file
    for version in ("v1", "v2", "v3"):
        await hook("edit", {"path": "base.txt"})
        target.write_text(version)
    store.undo_last(); store.undo_last()
    assert target.read_text() == "v1", f"LIFO restore failed: {target.read_text()!r}"
    store.undo_last()
    assert target.read_text() == "base\n"
    assert store.undo_last() is None
    print("PASS undo pops in reverse order; empty stack returns None")

    # ---- 3. full loop integration: write tool through ChatService records + undoes
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc, permission_gate=lambda n, a: asyncio.sleep(0, True),
                           pre_tool_hook=make_snapshot_hook(store))
        provider.queue = [{"id": "t9", "name": "write",
                           "arguments": {"path": "gen.txt", "content": "agent wrote this"}}]
        res = await chat.process_chat(message="create gen.txt please",
                                      session_id=None, provider_name="git-fake")
        gen = WS / "gen.txt"
        assert gen.read_text() == "agent wrote this"
        assert len(store.records) == 1 and store.records[0].before is None
        assert store.undo_last().path == gen and not gen.exists()
    print("PASS ChatService pre-tool hook captures real write-tool runs end to end")

    # ---- 4. diff/stat/stage/commit round-trip vs HEAD
    target.write_text("v4\n")
    stat = git.diff_stat()
    text = git.diff_text()
    assert "base.txt" in stat and "-base" in text and "+v4" in text
    git.stage(["base.txt"])
    sha2 = git.commit("test: bump base to v4")
    assert git.changed_paths() == [], "working tree must be clean after commit"
    print(f"PASS diff shows the change; stage+commit lands ({sha2})")

    # untracked files show up as changes too
    (WS / "untracked.log").write_text("x")   # ignored by .gitignore -> not listed
    (WS / "new_file.py").write_text("print(1)\n")
    changed = git.changed_paths()
    assert changed == ["new_file.py"], changed
    print("PASS changed_paths lists untracked work and respects .gitignore")

    # ---- 5. commit-message generation: scripted success + failure fallback
    msg = await generate_commit_message(provider, stat="1 file, +1 -1", excerpt="+v5")
    assert msg == "feat: add note generator", msg
    boom = await generate_commit_message(BoomProvider(), stat="", excerpt="")
    assert boom == "", "failure must yield empty string so callers fall back"
    fallback = f"chore: update {'base.txt'}" if not boom else boom
    assert fallback.startswith("chore:")
    print("PASS generate_commit_message (LLM subject; graceful failure fallback)")

    print("\nALL ASK 8 CHECKS PASSED")


try:
    asyncio.run(main())
finally:
    asyncio.run(engine.dispose())
    shutil.rmtree(WS, ignore_errors=True)
    Path("test_git.db").unlink(missing_ok=True)
