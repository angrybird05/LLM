"""Live validation of Ask 6 compaction against the real Anthropic API.

One small summarize() call — proves the force-compaction path end to end:
prepare -> split -> serialize -> real LLM summary -> persist -> stats.
"""
import asyncio
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_compact_live.db"
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.models.database import async_session_maker, engine, init_db  # noqa: E402
from coding_agent.models.orm import Role  # noqa: E402
from coding_agent.models.schemas import MessageCreate, SessionCreate  # noqa: E402
from coding_agent.services.chat_service import ChatService  # noqa: E402
from coding_agent.services.session_service import SessionService  # noqa: E402

CONVO = [
    (Role.USER, "Project context: we're building a payments microservice in FastAPI. "
                "Decision made today: use Stripe Checkout, not Elements."),
    (Role.ASSISTANT, "Understood - Stripe Checkout chosen over Elements."),
    (Role.USER, "Bug fixed today: checkout returned 500 because the webhook secret was "
                "read from WEBHOOK_SECRET instead of STRIPE_WEBHOOK_SECRET."),
    (Role.ASSISTANT, "Confirmed fix: renamed the env lookup to STRIPE_WEBHOOK_SECRET."),
    (Role.USER, "Reminder: the refund endpoint still lacks idempotency keys, add them "
                "next sprint."),
    (Role.ASSISTANT, "Logged: refund endpoint idempotency keys are a next-sprint task."),
]


async def main():
    await init_db()
    async with async_session_maker() as db:
        svc = SessionService(db)
        chat = ChatService(svc)
        session = await svc.create(SessionCreate())
        sid = session.id

        # Pad past COMPACT_KEEP_RECENT (16) so the force-split point lands
        # AFTER the real conversation — all of CONVO gets summarized, only
        # filler stays verbatim. 22 msgs - 16 kept = split at index 6.
        for role, content in CONVO:
            await svc.add_message(sid, MessageCreate(role=role, content=content))
        for i in range(len(CONVO), 22):
            role = Role.USER if i % 2 == 0 else Role.ASSISTANT
            await svc.add_message(sid, MessageCreate(
                role=role, content=f"filler turn {i}: minor back-and-forth about logging levels."))

        stats = await chat.compact(sid, provider_name="anthropic")
        assert stats, f"expected stats, got {stats}"
        print(f"stats: {stats}")

        loaded = await svc.get(sid)
        meta = loaded.session_metadata or {}
        summary = meta.get("compact_summary", "")
        assert summary, "summary must be persisted in session metadata"
        print(f"\n--- persisted summary ({len(summary)} chars) ---")
        print(summary[:600])
        low = summary.lower()
        for needle in ("stripe", "webhook", "idempoten"):
            assert needle in low, f"summary should mention {needle!r}"

    await engine.dispose()
    Path("test_compact_live.db").unlink(missing_ok=True)
    print("\nLIVE ASK 6 CHECK PASSED")


asyncio.run(main())
