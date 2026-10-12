# 🤖 Code Agent — AI-Powered Autonomous Coding Assistant

> A full-stack AI coding agent with a FastAPI backend, interactive terminal UI (TUI), tool execution, Docker sandboxing, LLM multi-provider support, and a TypeScript/LangGraph-based autonomous agent.

---

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup Guide](#setup-guide)
  - [1. Clone the Repository](#1-clone-the-repository)
  - [2. Python Backend Setup](#2-python-backend-setup)
  - [3. TypeScript Agent Setup (coding-agent/)](#3-typescript-agent-setup-coding-agent)
  - [4. Docker Setup (Recommended)](#4-docker-setup-recommended)
  - [5. Database Setup](#5-database-setup)
  - [6. Environment Configuration](#6-environment-configuration)
- [Running the Application](#running-the-application)
- [API Reference](#api-reference)
- [CLI Commands](#cli-commands)
- [Available Tools](#available-tools)
- [Configuration Reference](#configuration-reference)
- [Development](#development)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Overview

**Code Agent** is a comprehensive AI coding assistant that combines:

1. **Python FastAPI Backend** — REST API server with streaming chat, session management, file upload/download, and Docker sandbox code execution.
2. **Interactive Terminal UI (TUI)** — Rich terminal-based interface built with [Textual](https://textual.textualize.io/) and [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/) for a premium coding experience.
3. **TypeScript LangGraph Agent** (`coding-agent/`) — Autonomous agent with LangGraph, LangChain, pgvector-based memory, and multi-provider LLM support (OpenAI, Anthropic, Groq, Ollama).
4. **Multi-Provider LLM Support** — Anthropic (Claude), OpenAI, OpenRouter, Groq, and local models via Ollama.
5. **Tool System** — File operations, shell execution, web scraping, git operations, and more.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       Code Agent                              │
├──────────────────┬───────────────────────────────────────────┤
│  Python Backend  │  TypeScript Agent (coding-agent/)          │
│  (FastAPI)       │  (LangGraph + LangChain)                  │
├──────────────────┼───────────────────────────────────────────┤
│                  │                                            │
│  ┌─────────────┐ │  ┌──────────────┐  ┌───────────────────┐  │
│  │ REST API    │ │  │ LangGraph    │  │ Vector Memory     │  │
│  │ /api/v1/*   │ │  │ Agent Loop   │  │ (pgvector)        │  │
│  └─────────────┘ │  └──────────────┘  └───────────────────┘  │
│                  │                                            │
│  ┌─────────────┐ │  ┌──────────────┐  ┌───────────────────┐  │
│  │ Chat Service│ │  │ Tool System  │  │ Ollama Embeddings │  │
│  │ (Streaming) │ │  │ (FS, Shell,  │  │ (Local)           │  │
│  └─────────────┘ │  │  Web, Git)   │  └───────────────────┘  │
│                  │  └──────────────┘                          │
│  ┌─────────────┐ │                                            │
│  │ TUI / NEXUS │ │  ┌──────────────┐                         │
│  │ (Textual)   │ │  │ Eval Harness │                         │
│  └─────────────┘ │  └──────────────┘                         │
│                  │                                            │
│  ┌─────────────┐ │                                            │
│  │ Docker      │ │                                            │
│  │ Sandbox     │ │                                            │
│  └─────────────┘ │                                            │
└──────────────────┴───────────────────────────────────────────┘
         │                      │
    ┌────┴────┐           ┌────┴────┐
    │ SQLite/ │           │ Postgres│
    │ Postgres│           │ pgvector│
    └─────────┘           └─────────┘
```

---

## Features

### Python Backend
- **FastAPI** — Modern async Python web framework with automatic OpenAPI docs
- **Streaming Chat** — Server-Sent Events (SSE) and WebSocket support for real-time responses
- **LLM Providers** — Anthropic (Claude), OpenAI, and OpenRouter support with factory pattern
- **Tool System** — File operations (read/write/glob/grep), shell commands, web fetch/search
- **Docker Sandbox** — Secure code execution in isolated containers with resource limits
- **Database** — Async SQLAlchemy with SQLite (dev) and PostgreSQL (prod), Alembic migrations
- **Redis** — Rate limiting with slowapi
- **API Key Auth** — API key + JWT Bearer authentication middleware
- **File Upload** — Secure file upload/download with extension filtering
- **Permission System** — Configurable permission modes (default, auto-edit, full-auto)
- **Session Management** — Persistent chat sessions with message history
- **Context Builder** — Smart system prompt construction with project context
- **Git Integration** — Git operations service (diff, commit, blame, log)
- **Message Compaction** — Automatic conversation context compaction to manage token limits

### Terminal UI (TUI)
- **Interactive REPL** — Rich terminal interface with syntax highlighting
- **NEXUS Console** — Full-screen Textual-based TUI with widgets and animations
- **Command System** — Slash commands for session management, mode switching
- **Session Resume** — Continue or resume previous chat sessions
- **Metrics Dashboard** — Token usage, timing, and performance metrics

### TypeScript Agent (coding-agent/)
- **LangGraph** — State-machine-based agent loop with planning and execution
- **Multi-Provider** — OpenAI, Anthropic, Groq, Ollama via LangChain adapters
- **pgvector Memory** — Vector-based semantic memory with embeddings
- **Eval Harness** — Built-in evaluation framework for agent testing
- **Structured Prompts** — System, planning, and subagent prompt templates

---

## Project Structure

```
codeAgent/
├── .env                          # Environment variables (API keys, config)
├── .env.example                  # Template for environment variables
├── .gitignore                    # Git ignore rules
├── Dockerfile                    # Multi-stage Docker build
├── docker-compose.yml            # Full stack: app + PostgreSQL + Redis
├── pyproject.toml                # Python project config & dependencies
├── requirements.txt              # Detailed Python dependencies with comments
├── alembic.ini                   # Alembic migration configuration
├── MEMORY.md                     # Codebase architecture reference
├── TASKS.md                      # Development task tracker
│
├── src/coding_agent/             # ══ Python Backend ══
│   ├── __init__.py
│   ├── main.py                   # FastAPI app + Typer CLI entry point
│   ├── config.py                 # Pydantic Settings (env-based config)
│   │
│   ├── api/                      # API layer
│   │   ├── middleware/
│   │   │   ├── auth.py           # API Key + JWT authentication
│   │   │   ├── logging.py        # Request logging with request IDs
│   │   │   └── rate_limit.py     # Redis-backed rate limiting
│   │   └── routes/
│   │       ├── health.py         # GET /health (DB + Redis checks)
│   │       ├── chat.py           # POST /chat, GET /chat/stream, tools
│   │       ├── sessions.py       # CRUD for chat sessions
│   │       └── files.py          # File upload/download
│   │
│   ├── core/                     # Core business logic
│   │   ├── context_builder.py    # System prompt + project context
│   │   ├── hooks.py              # Lifecycle hooks for agent events
│   │   ├── permissions.py        # Permission modes system
│   │   ├── llm/                  # LLM provider abstraction
│   │   │   ├── base.py           # Abstract LLMProvider
│   │   │   ├── anthropic.py      # Anthropic (Claude) provider
│   │   │   ├── openai.py         # OpenAI/OpenRouter provider
│   │   │   └── factory.py        # Provider registry & factory
│   │   ├── tools/                # Tool implementations
│   │   │   ├── base.py           # ToolRegistry
│   │   │   ├── filesystem.py     # read, write, list, glob, grep
│   │   │   ├── shell.py          # bash execution (Docker sandbox)
│   │   │   └── web.py            # web_fetch, web_search
│   │   └── sandbox/
│   │       └── docker.py         # Docker container sandbox
│   │
│   ├── models/                   # Data models
│   │   ├── database.py           # Async SQLAlchemy engine & sessions
│   │   ├── orm.py                # ORM models (Session, Message, ToolCall)
│   │   └── schemas.py            # Pydantic request/response schemas
│   │
│   ├── services/                 # Service layer
│   │   ├── chat_service.py       # Chat orchestration (LLM + tools)
│   │   ├── session_service.py    # Session CRUD operations
│   │   ├── compactor.py          # Conversation context compaction
│   │   └── git_service.py        # Git operations service
│   │
│   ├── tui/                      # Terminal UI (prompt_toolkit)
│   │   ├── app.py                # Interactive REPL
│   │   ├── commands.py           # Slash command handlers
│   │   ├── input_line.py         # Custom input widget
│   │   └── renderer.py           # Rich output renderer
│   │
│   └── ui/                       # Full-screen TUI (Textual)
│       ├── app.py                # NEXUS console application
│       ├── animations.py         # UI animations
│       ├── commands.py           # TUI command system
│       ├── metrics.py            # Performance metrics display
│       ├── theme.py              # Color theme definitions
│       └── widgets/              # Custom Textual widgets
│
├── alembic/                      # Database migrations
│   ├── env.py                    # Migration environment
│   └── versions/                 # Migration scripts
│
├── tests/                        # Test suite
│   ├── conftest.py               # Pytest fixtures
│   └── test_health.py            # Health endpoint tests
│
├── coding-agent/                 # ══ TypeScript Agent ══
│   ├── .env                      # TypeScript agent config
│   ├── .env.example              # Config template
│   ├── package.json              # Node.js dependencies
│   ├── tsconfig.json             # TypeScript configuration
│   ├── pyproject.toml            # Python bindings config
│   │
│   ├── src/
│   │   ├── agent/                # Agent loop & state machine
│   │   ├── cli/                  # CLI entry point
│   │   ├── coding_agent/         # Core agent logic
│   │   ├── config/               # Configuration management
│   │   ├── db/                   # Database (PostgreSQL + pgvector)
│   │   ├── llm/                  # LLM provider adapters
│   │   ├── memory/               # Vector-based semantic memory
│   │   ├── sandbox/              # Code execution sandbox
│   │   └── tools/                # Tool implementations
│   │
│   ├── prompts/                  # Prompt templates
│   │   ├── system.md             # System prompt
│   │   ├── planning.md           # Planning prompt
│   │   └── subagent.md           # Sub-agent prompt
│   │
│   ├── evals/                    # Evaluation harness
│   │   ├── harness.ts            # Eval runner
│   │   └── tasks/                # Eval task definitions
│   │
│   ├── data/                     # Data files
│   ├── dist/                     # Compiled output
│   └── tests/                    # TypeScript tests
│
├── verify_*.py                   # Verification/validation scripts
├── test_*.py                     # Additional test scripts
└── cmd.txt                       # Command reference
```

---

## Prerequisites

Before setting up the project, ensure you have the following installed:

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | ≥ 3.11 | Backend runtime |
| **Node.js** | ≥ 18.x | TypeScript agent runtime |
| **npm** | ≥ 9.x | Node package manager |
| **Docker** | ≥ 24.x | Sandbox execution & containerized deployment |
| **Docker Compose** | ≥ 2.x | Multi-container orchestration |
| **Git** | ≥ 2.x | Version control |
| **PostgreSQL** | ≥ 15 (optional) | Production database (SQLite used for dev) |
| **Redis** | ≥ 7.x (optional) | Rate limiting (disabled by default in dev) |
| **ripgrep** | Latest (optional) | Fast file search for grep tool |
| **Ollama** | Latest (optional) | Local embeddings & models |

---

## Setup Guide

### 1. Clone the Repository

```bash
git clone https://github.com/angrybird05/LLM.git
cd LLM
```

### 2. Python Backend Setup

#### Option A: Using pip (Recommended for Development)

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Windows (CMD):
.\.venv\Scripts\activate.bat
# macOS/Linux:
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# OR install from requirements.txt
pip install -r requirements.txt
```

#### Option B: Using uv (Faster Alternative)

```bash
# Install uv if not already installed
pip install uv

# Create environment and install
uv venv .venv
source .venv/bin/activate  # or .\.venv\Scripts\Activate.ps1 on Windows
uv pip install -e ".[dev]"
```

### 3. TypeScript Agent Setup (coding-agent/)

```bash
cd coding-agent

# Install Node.js dependencies
npm install

# Build TypeScript
npm run build

# Return to root
cd ..
```

### 4. Docker Setup (Recommended)

For the full production-like stack with PostgreSQL, Redis, and the app:

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your API keys (see Configuration section)

# Start all services
docker-compose up -d

# Check services are running
docker-compose ps

# View logs
docker-compose logs -f app
```

### 5. Database Setup

#### SQLite (Development — Zero Configuration)

SQLite works out of the box. The database file `dev.db` is created automatically.

```bash
# Initialize the database
coding-agent init-db

# Run migrations
coding-agent migrate
```

#### PostgreSQL (Production)

```bash
# Start PostgreSQL via Docker Compose
docker-compose up -d db

# Or connect to existing PostgreSQL
# Update DATABASE_URL in .env:
# DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/coding_agent

# Run migrations
coding-agent migrate
```

#### PostgreSQL with pgvector (For TypeScript Agent)

```bash
cd coding-agent

# Start PostgreSQL with pgvector
docker run -d \
  --name pgvector \
  -e POSTGRES_DB=coding_agent \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5433:5432 \
  pgvector/pgvector:pg16

# Run database migrations
npm run db:migrate

cd ..
```

### 6. Environment Configuration

The project uses `.env` files for configuration. Two `.env` files exist:

#### Root `.env` (Python Backend)

```env
# App
APP_HOST=0.0.0.0
APP_PORT=8000
APP_ENV=development
LOG_LEVEL=INFO

# Database
DATABASE_URL=sqlite+aiosqlite:///./dev.db

# Redis (optional in dev)
REDIS_URL=redis://localhost:6379/0

# Authentication
API_KEYS=sk-dev-key-1,sk-dev-key-2
JWT_SECRET=dev-secret-change-in-production-min-32-chars
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# Rate Limiting
RATE_LIMIT_ENABLED=false
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_WINDOW=60

# LLM Providers (add your keys)
ANTHROPIC_API_KEY=your-anthropic-key
OPENAI_API_KEY=your-openai-key
OPENROUTER_API_KEY=your-openrouter-key
DEFAULT_PROVIDER=openrouter
DEFAULT_MODEL=stealth/ox-alpha

# Docker Sandbox
DOCKER_HOST=unix:///var/run/docker.sock
SANDBOX_TIMEOUT=300
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_LIMIT=1.0
SANDBOX_IMAGE=python:3.11-slim

# File Upload
UPLOAD_DIR=/app/uploads
MAX_UPLOAD_SIZE=10485760
ALLOWED_EXTENSIONS=.py,.js,.ts,.json,.yaml,.yml,.md,.txt,.csv,.html,.css
```

#### `coding-agent/.env` (TypeScript Agent)

```env
# LLM Provider
OPENROUTER_API_KEY=your-openrouter-key
LLM_PROVIDER=openrouter
LLM_MODEL=meta-llama/llama-3.1-70b-instruct

# Local Models (Ollama)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_MODEL=qwen2.5-coder:7b

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/coding_agent
SQLITE_DB_PATH=./data/coding-agent.db

# Agent Settings
AGENT_MAX_ITERATIONS=50
AGENT_TOKEN_BUDGET=128000
AGENT_TEMPERATURE=0.1

# Logging
LOG_LEVEL=debug
LOG_PRETTY=true

# Sandbox
SANDBOX_WORKSPACE=/tmp/coding-agent-workspace
SANDBOX_TIMEOUT_MS=30000

# Vector Config
PGVECTOR_DIMENSIONS=768
DB_POOL_SIZE=10
```

---

## Running the Application

### Interactive Terminal (Default)

```bash
# Launch the interactive REPL
coding-agent

# With permission mode
coding-agent --mode auto-edit
coding-agent --mode full-auto

# Resume last session
coding-agent --continue

# Resume specific session
coding-agent --resume <session-id-prefix>
```

### NEXUS Full-Screen Console

```bash
coding-agent tui
coding-agent tui --mode full-auto
```

### FastAPI Server

```bash
# Development (with auto-reload)
coding-agent serve --reload

# Production
coding-agent serve --host 0.0.0.0 --port 8000

# Access the API docs
# Swagger UI: http://localhost:8000/docs
# ReDoc:      http://localhost:8000/redoc
```

### One-Shot CLI Chat

```bash
# Simple chat
coding-agent chat "create a hello world python file"

# Streaming response
coding-agent chat --stream "explain this code"

# With specific provider/model
coding-agent chat --provider openrouter --model meta-llama/llama-3.1-70b-instruct "fix the bug"
```

### TypeScript Agent

```bash
cd coding-agent

# Development mode
npm run dev

# Production
npm start

# Run evaluations
npm run eval
```

### Docker Compose (Full Stack)

```bash
# Start everything
docker-compose up -d

# Health check
curl http://localhost:8000/health

# Stop everything
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

---

## API Reference

### Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check with DB & Redis status |

### Chat
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/chat` | Non-streaming chat |
| `GET` | `/api/v1/chat/stream` | SSE streaming chat |
| `POST` | `/api/v1/tools/execute` | Execute a single tool |
| `GET` | `/api/v1/tools` | List available tools |

### Sessions
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/sessions` | Create a new session |
| `GET` | `/api/v1/sessions` | List all sessions |
| `GET` | `/api/v1/sessions/{id}` | Get session with messages |
| `DELETE` | `/api/v1/sessions/{id}` | Delete session |

### Files
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/files/upload` | Upload a file |
| `GET` | `/api/v1/files/{file_id}` | Download a file |

### Authentication

All API endpoints (except `/health`) require authentication via:
- **API Key**: `X-API-Key: sk-dev-key-1` header
- **JWT Bearer**: `Authorization: Bearer <token>` header

Example:
```bash
curl -H "X-API-Key: sk-dev-key-1" http://localhost:8000/api/v1/sessions
```

---

## CLI Commands

```bash
coding-agent                    # Launch interactive REPL (default)
coding-agent tui                # Launch NEXUS full-screen console
coding-agent serve              # Start FastAPI server
coding-agent serve --reload     # Start with auto-reload
coding-agent chat "message"     # One-shot chat
coding-agent chat --stream "msg"# Streaming chat
coding-agent init-db            # Initialize database tables
coding-agent migrate            # Run Alembic migrations
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `read` | Read file contents |
| `write` | Write/create file contents |
| `list` | List directory contents |
| `glob` | Find files by glob pattern |
| `grep` | Search file contents (uses ripgrep if available) |
| `bash` | Execute shell command in Docker sandbox |
| `web_fetch` | Fetch and parse web page content |
| `web_search` | Search the web (configurable backend) |

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_HOST` | `0.0.0.0` | Server bind address |
| `APP_PORT` | `8000` | Server port |
| `APP_ENV` | `development` | Environment (development/production) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DATABASE_URL` | `sqlite+aiosqlite:///./dev.db` | Database connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `API_KEYS` | `sk-dev-key-1,sk-dev-key-2` | Comma-separated API keys |
| `JWT_SECRET` | — | JWT signing secret (≥32 chars) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENROUTER_API_KEY` | — | OpenRouter API key |
| `DEFAULT_PROVIDER` | `openrouter` | Default LLM provider |
| `DEFAULT_MODEL` | `stealth/ox-alpha` | Default model name |
| `RATE_LIMIT_ENABLED` | `false` | Enable rate limiting |
| `RATE_LIMIT_REQUESTS` | `100` | Requests per window |
| `RATE_LIMIT_WINDOW` | `60` | Window in seconds |
| `SANDBOX_TIMEOUT` | `300` | Sandbox timeout (seconds) |
| `SANDBOX_MEMORY_LIMIT` | `512m` | Container memory limit |
| `SANDBOX_CPU_LIMIT` | `1.0` | Container CPU limit |
| `SANDBOX_IMAGE` | `python:3.11-slim` | Docker image for sandbox |

---

## Development

### Code Quality

```bash
# Lint
ruff check src/

# Format
ruff format src/

# Type check
mypy src/

# All checks
ruff check src/ && ruff format --check src/ && mypy src/
```

### Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# View migration history
alembic history
```

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/coding_agent

# Run specific test
pytest tests/test_health.py -v

# Run verification scripts
python verify_tui.py
python verify_commands.py
python verify_ui.py
```

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: coding_agent` | Run `pip install -e .` from root directory |
| Docker sandbox not working | Ensure Docker daemon is running and accessible |
| Database connection failed | Check `DATABASE_URL` in `.env` |
| Rate limiting errors | Set `RATE_LIMIT_ENABLED=false` in development |
| Redis connection refused | Start Redis or disable rate limiting |
| Port 8000 in use | Change `APP_PORT` in `.env` or use `--port` flag |
| Permission denied on Docker socket | Add user to `docker` group or use `sudo` |
| TypeScript build errors | Run `npm install` in `coding-agent/` |
| pgvector not found | Use `pgvector/pgvector:pg16` Docker image |

### Verifying Installation

```bash
# Check Python setup
python -c "from coding_agent.main import app; print('✅ Backend OK')"

# Check CLI
coding-agent --help

# Check API
curl http://localhost:8000/health

# Check Docker
docker ps
```

---

## License

MIT

---

> Built with ❤️ using FastAPI, Textual, LangGraph, and LangChain