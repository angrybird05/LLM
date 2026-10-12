import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from coding_agent.models.orm import Message, Session, ToolCall, ToolStatus
from coding_agent.models.schemas import MessageCreate, SessionCreate, ToolCallCreate

_TITLE_LIMIT = 40


def derive_title(text: str) -> str:
    """First-line, first-40-chars session title (deterministic fallback for
    auto-titling — no extra LLM call per new session)."""
    first_line = " ".join((text or "").split())
    if not first_line:
        return "(untitled)"
    if len(first_line) <= _TITLE_LIMIT:
        return first_line
    cut = first_line[:_TITLE_LIMIT]
    if " " in cut[_TITLE_LIMIT // 2:]:
        cut = cut.rsplit(" ", 1)[0]  # don't cut mid-word when avoidable
    return cut + "…"


class SessionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: SessionCreate) -> Session:
        session = Session(title=data.title, session_metadata=data.session_metadata)
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def get(self, session_id: uuid.UUID) -> Session | None:
        # populate_existing: without it, a Session already in the identity map
        # (created earlier in this request) keeps its stale loaded messages
        # collection and writes made through this context stay invisible.
        result = await self.db.execute(
            select(Session)
            .options(selectinload(Session.messages).selectinload(Message.tool_calls))
            .where(Session.id == session_id)
            .execution_options(populate_existing=True)
        )
        session = result.scalar_one_or_none()
        if session:
            session.messages.sort(
                key=lambda m: (m.seq if m.seq is not None else 0, m.created_at)
            )
        return session

    async def list(self, limit: int = 50, offset: int = 0) -> list[Session]:
        result = await self.db.execute(
            select(Session)
            .order_by(Session.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def most_recent(self) -> Session | None:
        """Newest session by update time — the `--continue` target."""
        session = (await self.list(limit=1) or [None])[0]
        if session:
            # Re-fetch fully loaded so callers see messages/tool_calls too
            session = await self.get(session.id)
        return session

    async def set_title(self, session_id: uuid.UUID, title: str) -> None:
        result = await self.db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return
        session.title = title[:500]
        await self.db.commit()

    async def delete(self, session_id: uuid.UUID) -> bool:
        session = await self.get(session_id)
        if session:
            await self.db.delete(session)
            await self.db.commit()
            return True
        return False

    async def set_metadata(self, session_id: uuid.UUID, patch: dict) -> None:
        """Merge keys into session_metadata. Reassigns the dict so SQLAlchemy
        detects the change on the JSON column."""
        result = await self.db.execute(select(Session).where(Session.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return
        meta = dict(session.session_metadata or {})
        meta.update(patch)
        session.session_metadata = meta
        await self.db.commit()

    async def add_message(self, session_id: uuid.UUID, data: MessageCreate) -> Message:
        # Assign the next per-session sequence number in the same transaction so
        # history order is exact even when several messages share a timestamp.
        next_seq = await self._next_seq(session_id)
        message = Message(session_id=session_id, role=data.role, content=data.content, seq=next_seq)
        self.db.add(message)
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def _next_seq(self, session_id: uuid.UUID) -> int:
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.max(Message.seq)).where(Message.session_id == session_id)
        )
        current = result.scalar_one_or_none()
        return (current or 0) + 1

    async def add_tool_call(self, message_id: uuid.UUID, data: ToolCallCreate) -> ToolCall:
        tool_call = ToolCall(
            message_id=message_id,
            name=data.name,
            arguments=data.arguments,
            tool_use_id=data.tool_use_id,
        )
        self.db.add(tool_call)
        await self.db.commit()
        await self.db.refresh(tool_call)
        return tool_call

    async def update_tool_call(
        self, tool_call_id: uuid.UUID, result: dict, status: ToolStatus
    ) -> ToolCall | None:
        from datetime import datetime
        result_obj = await self.db.execute(select(ToolCall).where(ToolCall.id == tool_call_id))
        tool_call = result_obj.scalar_one_or_none()
        if tool_call:
            tool_call.result = result
            tool_call.status = status
            tool_call.completed_at = datetime.utcnow()
            await self.db.commit()
            await self.db.refresh(tool_call)
        return tool_call

    async def recent_pending_tool_calls(
        self, session_id: uuid.UUID, limit: int = 20
    ) -> list[ToolCall]:
        """Most recent PENDING tool_call rows for a session (newest first)."""
        result = await self.db.execute(
            select(ToolCall)
            .join(Message, ToolCall.message_id == Message.id)
            .where(Message.session_id == session_id, ToolCall.status == ToolStatus.PENDING)
            .order_by(Message.seq.desc().nullslast(), ToolCall.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
