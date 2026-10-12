import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str | None = None
    session_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    session_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class MessageBase(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str


class MessageCreate(MessageBase):
    pass


class ToolCallBase(BaseModel):
    name: str
    arguments: dict[str, Any]
    tool_use_id: str | None = None


class ToolCallCreate(ToolCallBase):
    pass


class ToolCallResponse(ToolCallBase):
    id: uuid.UUID
    result: dict[str, Any] | None
    status: Literal["pending", "success", "error"]
    started_at: datetime | None = None
    completed_at: datetime | None = None


class MessageResponse(MessageBase):
    id: uuid.UUID
    session_id: uuid.UUID
    created_at: datetime | None = None
    tool_calls: list[ToolCallResponse] = []


class ChatRequest(BaseModel):
    message: str
    session_id: uuid.UUID | None = None
    model: str | None = None
    provider: str | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    tools: list[str] | None = None


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    message: MessageResponse


class ToolExecuteRequest(BaseModel):
    name: str
    arguments: dict[str, Any]
    session_id: uuid.UUID | None = None


class ToolExecuteResponse(BaseModel):
    name: str
    result: dict[str, Any]
    status: Literal["success", "error"]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    redis: str


class FileUploadResponse(BaseModel):
    filename: str
    path: str
    size: int
    content_type: str
