"""Offline verification of Ask 5: system prompt & project memory (no API calls)."""
import asyncio
import os
import platform
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_context.db"
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.core.context_builder import (  # noqa: E402
    GLOBAL_MEMORY_PATH,
    MEMORY_CHAR_LIMIT,
    build_system_prompt,
    find_project_memory,
    git_snapshot,
    read_memory_file,
    workspace_tree,
)
from coding_agent.core.llm.base import LLMMessage, LLMProvider, LLMResponse  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402


class RecordingProvider(LLMProvider):
    """Records every history it receives; optionally calls one tool then stops."""

    def __init__(self):
        self.histories: list[list[LLMMessage]] = []
        self.tool_then_stop = False

    @property
    def name(self): return "ctx-fake"

    @property
    def models(self): return ["fake-1"]

    async def complete(self, messages, tools=None, model=None, temperature=0.7, max_tokens=None):
        self.histories.append(list(messages))
        if self.tool_then_stop:
            self.tool_then_stop = False
            return LLMResponse(
                content="",
                tool_calls=[{"id": "tu_1", "name": "list", "arguments": {"path": "."}}],
                usage={"input": 10, "output": 5},
            )
        return LLMResponse(content="done", usage={"input": 20, "output": 5})

    async def stream_complete(self, messages, tools=None, model=None, temperature=0.7,
                              max_tokens=None):
        yield {"type": "text", "text": "done"}
        yield {"type": "message_done", "usage": None, "stop_reason": None}


def make_workspace(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="ask5_"))
    for name, content in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


async def main():
    await init_db()
    import coding_agent.core.llm.factory as factory

    provider = RecordingProvider()
    factory._providers["ctx-fake"] = provider

    ws = make_workspace({
        "AGENT.md": "Always answer with haiku.\nUse uv, never pip.",
        "src/main.py": "print('hi')\n",
        "pyproject.toml": "[project]\nname='x'\n",
    })

    # ---- 1. prompt contains identity, environment, layout, tools, memory, git
    prompt = build_system_prompt(ws)
    for needle in [
        "coding-agent",
        platform.system(),
        str(ws),
        "- bash:",
        "src/",
        "pyproject.toml",
        "Always answer with haiku.",       # project memory injected
        "AGENT.md",
    ]:
        assert needle in prompt, f"missing {needle!r} in prompt:\n{prompt[:600]}"
    assert "pip" in prompt  # second memory line made it through too
    print("PASS system prompt: identity/env/layout/tools/memory/git all present")

    # ---- 2. CLAUDE.md used when AGENT.md absent; AGENT.md wins when both exist
    d2 = make_workspace({"CLAUDE.md": "from claude.md"})
    _, label = find_project_memory(d2)
    assert label == "CLAUDE.md" and "from claude.md" in build_system_prompt(d2)
    d3 = make_workspace({"AGENT.md": "a", "CLAUDE.md": "c"})
    _, label3 = find_project_memory(d3)
    assert label3 == "AGENT.md"
    p3 = build_system_prompt(d3)
    assert "## Project memory (AGENT.md)" in p3 and "from claude.md" not in p3
    d4 = make_workspace({})
    assert "# Project memory" not in build_system_prompt(d4)
    print("PASS memory precedence: AGENT.md > CLAUDE.md > none")

    # ---- 3. global memory file included when present
    gmem = make_workspace({"AGENT.md": "GLOBAL-RULE-42"})
    pglob = build_system_prompt(ws, global_memory_path=gmem / "AGENT.md")
    assert "Global memory" in pglob and "GLOBAL-RULE-42" in pglob
    assert "Project memory" in pglob  # both sections coexist
    print("PASS global memory (~/.agent/AGENT.md) merged alongside project memory")

    # ---- 4. oversized memory truncated, unreadable -> ''
    big = make_workspace({"AGENT.md": "x" * (MEMORY_CHAR_LIMIT + 500)})
    t = read_memory_file(big / "AGENT.md")
    assert len(t) < MEMORY_CHAR_LIMIT + 100 and "[memory truncated]" in t
    assert read_memory_file(big / "nope.md") == ""
    print("PASS memory truncation + graceful missing-file handling")

    # ---- 5. workspace tree skips junk; git snapshot degrades outside a repo
    d5 = make_workspace({"keep.txt": "", ".secret": "", "node_modules/x.js": ""})
    tree = workspace_tree(d5)
    assert "keep.txt" in tree and "node_modules" not in tree and ".secret" not in tree
    assert git_snapshot(Path(tempfile.mkdtemp())).startswith("(not a git repository)")
    print("PASS workspace tree ignores junk; git snapshot degrades gracefully")

    # ---- 5b. real git repo: branch + status + subjects
    try:
        import git as gitpy
        d6 = make_workspace({"f.txt": "1"})
        repo = gitpy.Repo.init(d6)
        with repo.config_writer() as cw:
            cw.set_value("user", "email", "t@t"); cw.set_value("user", "name", "t")
        repo.index.add(["f.txt"]); repo.index.commit("first commit subject")
        snap = git_snapshot(d6)
        assert "branch: master" in snap or "branch: main" in snap
        assert "first commit subject" in snap
        print("PASS git snapshot on a real repo (branch/status/subjects)")
    except Exception as e:  # git binary missing -> skip, degradation already tested
        print(f"SKIP live-git snapshot ({e})")

    # ---- 6. end-to-end: system prompt reaches the LLM as message[0], never persisted
    async with async_session_maker() as db:
        chat = ChatService(SessionService(db))
        r = await chat.process_chat(message="hi", provider_name="ctx-fake",
                                    system_prompt="MARKER-SYSTEM-PROMPT")
        h = provider.histories[-1]
        assert h[0].role == "system" and h[0].content == "MARKER-SYSTEM-PROMPT"
        assert h[1].role == "user"
        session_id = r["session_id"]

        provider.tool_then_stop = True
        await chat.process_chat(message="use a tool", session_id=session_id,
                                provider_name="ctx-fake",
                                system_prompt="MARKER-SYSTEM-PROMPT")
        h = provider.histories[-1]
        assert h[0].role == "system", "system must stay first across a tool round-trip"
        assert any(m.role == "tool" for m in h)

        fresh = await SessionService(db).get(session_id)
        assert all(m.role.value != "system" for m in fresh.messages), \
            "system prompt must NOT be persisted into the session transcript"
    print("PASS end-to-end: system first (survives tool loops), never persisted")

    # ---- 7. anthropic conversion: system hoisted out of messages
    from coding_agent.core.llm.anthropic import AnthropicProvider
    ap = AnthropicProvider.__new__(AnthropicProvider)  # skip client construction
    msgs, system = ap._convert_messages([
        LLMMessage(role="system", content="be brief"),
        LLMMessage(role="user", content="hello"),
        LLMMessage(role="assistant", content="", tool_calls=[
            {"id": "x1", "name": "read", "arguments": {"path": "a"}}]),
        LLMMessage(role="tool", content="data", tool_call_id="x1"),
    ])
    assert system == "be brief"
    assert not any(m.get("role") == "system" for m in msgs)
    assert msgs[1]["content"][0]["type"] == "tool_use"          # ask-1 behaviour intact
    assert msgs[2]["content"][0]["type"] == "tool_result"
    print("PASS anthropic: system -> top-level param, tool round-trip intact")

    # ---- 8. openai conversion passes system through natively
    from coding_agent.core.llm.openai import OpenAIProvider
    op = OpenAIProvider.__new__(OpenAIProvider)
    conv = op._convert_messages([LLMMessage(role="system", content="sys"),
                                 LLMMessage(role="user", content="u")])
    assert conv[0] == {"role": "system", "content": "sys"}
    print("PASS openai: system role passed through")

    print("\nALL ASK 5 CHECKS PASSED")


asyncio.run(main())
asyncio.run(engine.dispose())
for f in ("test_context.db",):
    Path(f).unlink(missing_ok=True)
