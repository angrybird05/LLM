# Coding Agent Codebase Memory

## Overview
AI Coding Agent backend built with FastAPI, featuring chat sessions, LLM integration (Anthropic/OpenAI), tool execution (filesystem, shell, web), and Docker sandbox for code execution.

## Architecture

### Core Components
- **FastAPI App** (`src/coding_agent/main.py:26`) - Main entry point with lifespan, CORS, middleware, routers
- **Config** (`src/coding_agent/config.py:6`) - Pydantic Settings with env file support
- **Database** (`src/coding_agent/models/database.py:10`) - Async SQLAlchemy with SQLite/PostgreSQL
- **CLI** (`src/coding_agent/main.py:56`) - Typer commands: serve, chat, migrate, init_db

### Data Models (`src/coding_agent/models/orm.py`)
- **Session** - Chat sessions with title, metadata, timestamps
- **Message** - Role (user/assistant/tool/system), content, session FK
- **ToolCall** - Tool execution records with status, arguments, results

### Services
- **SessionService** (`src/coding_agent/services/session_service.py:10`) - CRUD for sessions/messages/tool_calls
- **ChatService** (`src/coding_agent/services/chat_service.py:13`) - Orchestrates LLM + tools, handles streaming

### LLM Providers (`src/coding_agent/core/llm/`)
- **Base** (`base.py:25`) - Abstract LLMProvider with complete/stream_complete
- **Anthropic** (`anthropic.py:8`) - AsyncAnthropic client, tool_use format
- **OpenAI** (`openai.py:8`) - AsyncOpenAI client, supports OpenRouter
- **Factory** (`factory.py:10`) - Provider registry with lazy init

### Tools (`src/coding_agent/core/tools/`)
- **Registry** (`base.py:40`) - ToolRegistry with register/get/list
- **Filesystem** (`filesystem.py:9`) - read, write, list, glob, grep
- **Shell** (`shell.py:7`) - bash execution in Docker sandbox
- **Web** (`web.py:7`) - web_fetch, web_search (stub)

### Sandbox (`src/coding_agent/core/sandbox/docker.py:11`)
- DockerSandbox with container lifecycle, file copy, command execution
- Resource limits: memory, CPU, network disabled, auto-remove

### API Routes (`src/coding_agent/api/routes/`)
- **health** (`health.py:12`) - /health with DB/Redis checks
- **chat** (`chat.py:22`) - /chat, /chat/stream, /tools/execute, /tools
- **sessions** (`sessions.py:14`) - CRUD for sessions
- **files** (`files.py:22`) - upload/download with extension filtering

### Middleware
- **Auth** (`auth.py:29`) - API Key (X-API-Key) + JWT Bearer
- **Rate Limit** (`rate_limit.py:18`) - slowapi with Redis, per-API-key/IP
- **Logging** (`logging.py:9`) - Request ID, duration, structured logs

### Database Migrations
- Alembic (`alembic/versions/001_initial.py`) - Initial schema with UUID PKs, JSON columns, indexes

## Key Patterns
- Dependency injection via FastAPI Depends (db, services, auth)
- Async/await throughout with SQLAlchemy AsyncSession
- Tool execution: LLM calls tool → ChatService executes → result fed back to LLM
- Streaming: SSE endpoints with chunked responses
- Sandbox isolation: Docker containers per command with resource limits

## Configuration (.env)
- DATABASE_URL, REDIS_URL
- API_KEYS, JWT_SECRET
- ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY
- DOCKER_HOST, sandbox limits
- UPLOAD_DIR, MAX_UPLOAD_SIZE, ALLOWED_EXTENSIONS

## Running
```bash
# Install
pip install -e .

# Database
coding-agent migrate

# Server
coding-agent serve --reload

# CLI Chat
coding-agent chat "message" --session-id <uuid> --stream
```

## Testing
- pytest with asyncio (`tests/conftest.py`)
- Health test (`tests/test_health.py`)