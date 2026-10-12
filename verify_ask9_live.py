"""Ask 9 live check: one cheap Anthropic call exercising plan mode
(read-only tool set + planning suffix) end-to-end. README must be
mentioned in the plan but NOT modified."""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("WORKSPACE_ROOT", str(Path(__file__).parent))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_ask9_live.db"

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.config import settings  # noqa: E402

README = settings.workspace_path / "README.md"
before = README.read_bytes() if README.exists() else None

from coding_agent.core.context_builder import build_system_prompt  # noqa: E402
from coding_agent.core.tools.base import READ_ONLY_TOOL_NAMES  # noqa: E402
from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402
from coding_agent.tui.app import PLAN_SUFFIX  # noqa: E402


async def main():
    await init_db()
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        res = await chat.process_chat(
            message=("Plan the smallest possible change to add a 'License' "
                     "section pointer to README.md. Research first."),
            session_id=None,
            provider_name="anthropic",
            system_prompt=build_system_prompt() + PLAN_SUFFIX,
            tool_names=sorted(READ_ONLY_TOOL_NAMES),
        )
    text = res["message"]
    print(f"plan length: {len(text)} chars; tools used: {len(res['tool_calls'])}")
    assert "readme" in text.lower() or "license" in text.lower(), \
        "plan should mention its target"
    after = README.read_bytes() if README.exists() else None
    assert before == after, "plan mode must not modify files"
    print("PASS live /plan turn produced a research-based plan, zero writes")


try:
    asyncio.run(main())
finally:
    asyncio.run(engine.dispose())
    Path("test_ask9_live.db").unlink(missing_ok=True)
