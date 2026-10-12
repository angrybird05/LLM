from contextlib import asynccontextmanager

import typer
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from coding_agent.api.middleware.logging import RequestLoggingMiddleware
from coding_agent.api.middleware.rate_limit import setup_rate_limiting
from coding_agent.api.routes import chat_router, files_router, health_router, sessions_router
from coding_agent.config import settings
from coding_agent.models.database import engine, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up...")
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("Shutting down...")
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Coding Agent API",
        description="AI Coding Agent Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RequestLoggingMiddleware)
    if settings.RATE_LIMIT_ENABLED:
        setup_rate_limiting(app)

    app.include_router(health_router, tags=["health"])
    app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
    app.include_router(sessions_router, prefix="/api/v1", tags=["sessions"])
    app.include_router(files_router, prefix="/api/v1", tags=["files"])

    return app


app = create_app()


cli = typer.Typer(help="Coding Agent CLI")


@cli.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    mode: str = typer.Option(
        None, "--mode", "-m",
        help="Permission mode: default | auto-edit | full-auto",
    ),
    continue_: bool = typer.Option(
        False, "--continue", "-c",
        help="Resume the most recent session",
    ),
    resume: str = typer.Option(
        None, "--resume", "-r", is_flag=False, flag_value="",
        help="Resume a session: bare flag opens a picker, with an id-prefix resumes directly",
    ),
):
    """Launch the interactive terminal agent when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        from coding_agent.tui.app import run_repl
        run_repl(mode=mode, continue_recent=continue_, resume_prefix=resume)


@cli.command()
def tui(
    mode: str = typer.Option(
        None, "--mode", "-m",
        help="Permission mode: default | auto-edit | full-auto",
    ),
    continue_: bool = typer.Option(
        False, "--continue", "-c",
        help="Resume the most recent session",
    ),
    resume: str = typer.Option(
        None, "--resume", "-r", is_flag=False, flag_value="",
        help="Resume a session: bare flag opens a picker, with an id-prefix resumes directly",
    ),
):
    """Launch the NEXUS full-screen console (Textual)."""
    from coding_agent.ui.app import run_tui
    run_tui(mode=mode, continue_recent=continue_, resume_prefix=resume)


@cli.command()
def serve(
    host: str = settings.APP_HOST,
    port: int = settings.APP_PORT,
    reload: bool = False,
):
    """Start the FastAPI server."""
    uvicorn.run(
        "coding_agent.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=settings.LOG_LEVEL.lower(),
    )


@cli.command()
def chat(
    message: str,
    session_id: str = None,
    model: str = None,
    provider: str = None,
    stream: bool = False,
):
    """One-shot chat with the agent (non-interactive)."""
    import asyncio
    import json
    import uuid

    from coding_agent.core.context_builder import build_system_prompt
    from coding_agent.models.database import async_session_maker
    from coding_agent.services.chat_service import ChatService
    from coding_agent.services.session_service import SessionService

    async def run_chat():
        async with async_session_maker() as db:
            chat_service = ChatService(SessionService(db))
            session_uuid = uuid.UUID(session_id) if session_id else None
            system_prompt = build_system_prompt()

            if stream:
                async for event in chat_service.stream_chat(
                    message=message,
                    session_id=session_uuid,
                    provider_name=provider,
                    model=model,
                    system_prompt=system_prompt,
                ):
                    etype = event["type"]
                    if etype == "text":
                        print(event["text"], end="", flush=True)
                    elif etype == "tool_start":
                        print(f"\n[tool] {event['name']} {json.dumps(event['arguments'])[:120]}",
                              flush=True)
                    elif etype == "tool_result":
                        print(f"[tool done] {event['name']}", flush=True)
                    elif etype == "turn_done":
                        print(f"\n\nSession: {event['session_id']}")
                        print(f"Tokens: {event['usage']} over {event['iterations']} steps")
            else:
                result = await chat_service.process_chat(
                    message=message,
                    session_id=session_uuid,
                    provider_name=provider,
                    model=model,
                    system_prompt=system_prompt,
                )
                print(f"Session: {result['session_id']}")
                print(f"Response: {result['message']}")
                if result.get('tool_calls'):
                    for tc in result['tool_calls']:
                        summary = json.dumps(tc['result'], default=str)[:200]
                        print(f"Tool: {tc['name']} -> {summary}")

    asyncio.run(run_chat())


@cli.command()
def migrate():
    """Run database migrations."""
    import subprocess
    subprocess.run(["alembic", "upgrade", "head"])


@cli.command("init-db")
def init_db_cmd():
    """Initialize database tables."""
    import asyncio

    async def run_init():
        await init_db()
        print("Database initialized")

    asyncio.run(run_init())


if __name__ == "__main__":
    cli()
