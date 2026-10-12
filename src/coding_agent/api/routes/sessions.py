import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from coding_agent.api.middleware.auth import get_current_user
from coding_agent.models.database import get_db
from coding_agent.models.schemas import SessionCreate, SessionResponse
from coding_agent.services.session_service import SessionService

router = APIRouter()


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    request: Request,
    data: SessionCreate,
    user: dict = Depends(get_current_user),
    session_service: SessionService = Depends(lambda db=Depends(get_db): SessionService(db)),
):
    session = await session_service.create(data)
    return SessionResponse(
        id=session.id,
        title=session.title,
        session_metadata=session.session_metadata,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
    )


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    session_service: SessionService = Depends(lambda db=Depends(get_db): SessionService(db)),
):
    sessions = await session_service.list(limit=limit, offset=offset)
    return [
        SessionResponse(
            id=s.id,
            title=s.title,
            session_metadata=s.session_metadata,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=len(s.messages),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    session_service: SessionService = Depends(lambda db=Depends(get_db): SessionService(db)),
):
    session = await session_service.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(
        id=session.id,
        title=session.title,
        session_metadata=session.session_metadata,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    session_service: SessionService = Depends(lambda db=Depends(get_db): SessionService(db)),
):
    deleted = await session_service.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True}
