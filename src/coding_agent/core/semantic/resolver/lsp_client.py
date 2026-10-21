"""Optional Language Server Protocol (LSP) client adapter.

Provides an optional bridge to host language servers (e.g. pyright, pylsp,
gopls, typescript-language-server) via JSON-RPC over stdio when installed and
enabled in config.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from coding_agent.core.semantic.models import (
    ConfidenceLevel,
    ReferenceKind,
    Span,
    SymbolReference,
)

logger = logging.getLogger(__name__)


class LSPClientAdapter:
    """Lightweight asynchronous LSP client over stdio JSON-RPC."""

    def __init__(self, server_command: list[str], root_uri: str) -> None:
        self.server_command = server_command
        self.root_uri = root_uri
        self._process: asyncio.subprocess.Process | None = None
        self._req_id = 0
        self._pending_requests: dict[int, asyncio.Future[Any]] = {}
        self._is_initialized = False

    async def start(self) -> bool:
        """Launch the language server process."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.server_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Send initialize
            res = await self._send_request("initialize", {
                "processId": None,
                "rootUri": self.root_uri,
                "capabilities": {
                    "textDocument": {
                        "references": {"dynamicRegistration": False},
                        "rename": {"dynamicRegistration": False, "prepareSupport": True},
                    }
                },
            })
            if res:
                await self._send_notification("initialized", {})
                self._is_initialized = True
                return True
            return False
        except Exception as e:
            logger.debug("Failed to start LSP server %s: %s", self.server_command, e)
            return False

    async def find_references(
        self,
        file_path: Path,
        line: int,
        character: int,
    ) -> list[SymbolReference]:
        """Query textDocument/references from the language server."""
        if not self._is_initialized or not self._process:
            return []

        uri = file_path.as_uri()
        res = await self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": True},
        })

        if not res or not isinstance(res, list):
            return []

        refs: list[SymbolReference] = []
        for loc in res:
            try:
                target_uri = loc.get("uri", "")
                target_path = Path(target_uri.replace("file://", ""))
                rng = loc.get("range", {})
                start = rng.get("start", {})
                end = rng.get("end", {})
                span = Span(
                    start_byte=0,
                    end_byte=0,
                    start_line=start.get("line", 0),
                    start_column=start.get("character", 0),
                    end_line=end.get("line", 0),
                    end_column=end.get("character", 0),
                )
                refs.append(SymbolReference(
                    name="",
                    span=span,
                    file_path=target_path,
                    kind=ReferenceKind.USAGE,
                    confidence=ConfidenceLevel.DEFINITE,
                ))
            except Exception:
                continue
        return refs

    async def stop(self) -> None:
        """Shutdown and terminate the language server."""
        if self._process:
            try:
                await self._send_request("shutdown", None)
                await self._send_notification("exit", None)
                self._process.terminate()
            except Exception:
                pass
            finally:
                self._process = None
                self._is_initialized = False

    async def _send_request(self, method: str, params: Any) -> Any:
        if not self._process or not self._process.stdin:
            return None
        self._req_id += 1
        req_id = self._req_id
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        msg = json.dumps(payload)
        header = f"Content-Length: {len(msg)}\r\n\r\n"
        try:
            self._process.stdin.write((header + msg).encode("utf-8"))
            await self._process.stdin.drain()
            # Read response
            return await self._read_response(req_id)
        except Exception:
            return None

    async def _send_notification(self, method: str, params: Any) -> None:
        if not self._process or not self._process.stdin:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        msg = json.dumps(payload)
        header = f"Content-Length: {len(msg)}\r\n\r\n"
        try:
            self._process.stdin.write((header + msg).encode("utf-8"))
            await self._process.stdin.drain()
        except Exception:
            pass

    async def _read_response(self, expected_id: int) -> Any:
        if not self._process or not self._process.stdout:
            return None
        try:
            # Read headers
            content_length = 0
            while True:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=3.0)
                if not line:
                    return None
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str:
                    break
                if line_str.lower().startswith("content-length:"):
                    content_length = int(line_str.split(":", 1)[1].strip())

            if content_length <= 0:
                return None

            body = await asyncio.wait_for(self._process.stdout.readexactly(content_length), timeout=3.0)
            data = json.loads(body.decode("utf-8"))
            if data.get("id") == expected_id:
                return data.get("result")
        except Exception:
            return None
        return None
