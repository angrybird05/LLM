import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from coding_agent.api.middleware.auth import get_current_user
from coding_agent.config import settings
from coding_agent.core.permissions import PermissionManager
from coding_agent.core.tools.base import READ_ONLY_TOOL_NAMES, registry
from coding_agent.models.database import get_db
from coding_agent.models.schemas import (
    ChatRequest,
    ChatResponse,
    MessageResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
)
from coding_agent.services.chat_service import ChatService, ProviderTurnError
from coding_agent.services.session_service import SessionService

router = APIRouter()

_STATUS_BY_KIND = {"credits": 402, "too_long": 413}


def _headless_gate() -> PermissionManager:
    """Server-side permission gate. There is no human to approve anything over
    HTTP, so the manager runs with NO prompt_fn — its built-in fail-safe denies
    every risky tool unless the operator explicitly set PERMISSION_MODE to
    full-auto (danger-list commands still ask → still denied headless)."""
    return PermissionManager.from_mode(settings.PERMISSION_MODE)


def get_chat_service(db: AsyncSession = Depends(get_db)) -> ChatService:
    gate = _headless_gate()
    async def api_gate(name: str, arguments: dict) -> bool:
        decision = await gate.check(name, arguments)
        if not decision.allowed:
            from loguru import logger
            logger.warning(f"API tool call blocked by permission gate: {name} ({decision.reason})")
        return decision.allowed
    return ChatService(
        SessionService(db),
        permission_gate=api_gate,
        hook_runner=None,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    user: dict = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    if chat_request.stream:
        raise HTTPException(status_code=400, detail="Use /chat/stream for streaming")

    try:
        result = await chat_service.process_chat(
            message=chat_request.message,
            session_id=chat_request.session_id,
            provider_name=chat_request.provider,
            model=chat_request.model,
            temperature=chat_request.temperature or 0.7,
            max_tokens=chat_request.max_tokens or 4096,
            tool_names=chat_request.tools,
        )
    except ProviderTurnError as e:
        # Classified failure (credits/context/…): a clean status + reason,
        # not the provider's raw nested-JSON dump.
        raise HTTPException(
            status_code=_STATUS_BY_KIND.get(e.kind, 502),
            detail=e.detail[:300],
        ) from e
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        raise HTTPException(
            status_code=502, detail=f"LLM provider error: {str(e)}"
        ) from e

    # Build response message
    tool_calls = []
    for tc in result.get("tool_calls", []):
        tool_calls.append({
            "id": str(uuid.uuid4()),
            "name": tc["name"],
            "arguments": tc.get("arguments", {}),
            "result": tc.get("result"),
            "status": "error" if "error" in (tc.get("result") or {}) else "success",
            "started_at": None,
            "completed_at": None,
        })

    return ChatResponse(
        session_id=result["session_id"],
        message=MessageResponse(
            id=uuid.uuid4(),
            session_id=result["session_id"],
            role="assistant",
            content=result["message"],
            created_at=None,
            tool_calls=tool_calls,
        )
    )


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    message: str,
    session_id: str = None,
    model: str = None,
    provider: str = None,
    temperature: float = 0.7,
    max_tokens: int = None,
    user: dict = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service),
):
    session_uuid = uuid.UUID(session_id) if session_id else None

    async def generate():
        try:
            async for event in chat_service.stream_chat(
                message=message,
                session_id=session_uuid,
                provider_name=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except ProviderTurnError as e:
            yield f"data: {json.dumps({'type': 'error', 'kind': e.kind, 'error': e.detail[:300]})}\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    request: Request,
    tool_request: ToolExecuteRequest,
    user: dict = Depends(get_current_user),
):
    """Run one registered tool over HTTP.

    Audit A2: only read-only tools execute directly. Risky tools require the
    explicit TOOLS_EXECUTE_ENABLED opt-in AND pass the same headless permission
    gate as every other server-side path."""
    tool = registry.get(tool_request.name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_request.name}")

    if tool_request.name not in READ_ONLY_TOOL_NAMES and not settings.TOOLS_EXECUTE_ENABLED:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only read-only tools may execute via this endpoint. Set "
                "TOOLS_EXECUTE_ENABLED=true to allow risky tools (they still "
                "pass the server-side permission gate)."
            ),
        )

    arguments = dict(tool_request.arguments or {})
    gate = _headless_gate()
    decision = await gate.check(tool_request.name, arguments)
    if not decision.allowed:
        logger.warning(
            f"API /tools/execute blocked by permission gate: "
            f"{tool_request.name} ({decision.reason})"
        )
        raise HTTPException(
            status_code=403,
            detail=f"Permission gate denied tool '{tool_request.name}': {decision.reason}",
        )

    result = await tool.execute(arguments, {
        "session_id": str(tool_request.session_id) if tool_request.session_id else None,
        "workspace_root": str(settings.workspace_path),
    })

    return ToolExecuteResponse(
        name=tool_request.name,
        result=result,
        status="success" if result.get("success", True) else "error",
    )


@router.get("/tools")
async def list_tools(user: dict = Depends(get_current_user)):
    return {"tools": registry.get_schemas()}
