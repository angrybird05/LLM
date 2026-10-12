"""Web tools. web_fetch is SSRF-guarded (audit A6): every redirect hop's host
is DNS-resolved and refused when loopback/private/link-local/reserved unless
WEB_FETCH_ALLOW_PRIVATE=true, and the response body is size-capped so a huge
page can't be slurped into model context."""
import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from coding_agent.config import settings
from coding_agent.core.tools.base import Tool, ToolParameter, ToolSchema, registry

MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5


async def assert_fetchable(url: str) -> str | None:
    """Refusal reason for a URL that must not be fetched, else None.

    Resolves the host and refuses loopback/private/link-local/reserved/
    link-local-multicast addresses unless explicitly allowed."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"scheme {parsed.scheme!r} not supported (use http/https)"
    host = parsed.hostname or ""
    if not host:
        return "URL has no host"
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as e:
        return f"cannot resolve host: {e}"
    allow_private = getattr(settings, "WEB_FETCH_ALLOW_PRIVATE", False)
    if allow_private:
        return None
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return f"refusing non-public host '{host}' (resolved to {addr})"
    return None


class WebFetchTool(Tool):
    @property
    def name(self) -> str: return "web_fetch"

    @property
    def description(self) -> str: return "Fetch and parse a public web page"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "url": ToolParameter(type="string", description="HTTP(S) URL to fetch"),
            "selector": ToolParameter(type="string", description="CSS selector to extract"),
        }, required=["url"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url") or "")
        if not url:
            return {"error": "web_fetch requires a 'url' argument"}
        blocked = await assert_fetchable(url)
        if blocked:
            return {"error": f"web_fetch blocked: {blocked}"}

        current = url
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False,
                headers=headers,
            ) as client:
                body = b""
                status = 0
                for _hop in range(MAX_REDIRECTS + 1):
                    blocked = await assert_fetchable(current)
                    if blocked:
                        return {"error": f"web_fetch blocked: {blocked}"}
                    req = client.build_request("GET", current)
                    resp = await client.send(req, stream=True)
                    status = resp.status_code
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location", "")
                        await resp.aclose()
                        if not loc:
                            return {"error": f"redirect without Location header (status {status})"}
                        current = urljoin(current, loc)
                        continue
                    try:
                        resp.raise_for_status()
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                            total += len(chunk)
                            if total >= MAX_RESPONSE_BYTES:
                                break
                    finally:
                        await resp.aclose()
                    body = b"".join(chunks)[:MAX_RESPONSE_BYTES]
                    break
                else:
                    return {"error": f"too many redirects (> {MAX_REDIRECTS})"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code} fetching {current}"}
        except Exception as e:
            return {"error": f"fetch failed: {type(e).__name__}: {e}"}

        try:
            html = body.decode(resp.encoding or "utf-8", errors="replace")
        except Exception:
            html = body.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        if selector := arguments.get("selector"):
            elements = soup.select(selector)
            content = "\n".join(e.get_text(strip=True) for e in elements)
        else:
            content = soup.get_text(strip=True)[:5000]
        return {
            "url": current,
            "content": content,
            "title": soup.title.string if soup.title and soup.title.string else "",
        }


class WebSearchTool(Tool):
    """Web search via DuckDuckGo's HTML endpoint — no API key required."""

    @property
    def name(self) -> str: return "web_search"

    @property
    def description(self) -> str: return "Search the web"

    @property
    def parameters(self) -> ToolSchema:
        return ToolSchema(properties={
            "query": ToolParameter(type="string", description="Search query"),
            "num_results": ToolParameter(type="integer", description="Number of results (max 8)"),
        }, required=["query"])

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        query = arguments["query"]
        num = min(int(arguments.get("num_results") or 5), 8)
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                follow_redirects=True,
            ) as client:
                resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
                resp.raise_for_status()
        except Exception as e:
            return {"error": f"Search request failed: {e}"}

        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for block in soup.select(".result"):
            link = block.select_one("a.result__a")
            snippet = block.select_one(".result__snippet")
            if not link:
                continue
            href = link.get("href", "")
            # DDG wraps URLs in /l/?uddg=<urlencoded>; unwrap when present
            if "uddg=" in href:
                from urllib.parse import parse_qs
                try:
                    href = parse_qs(urlparse(href).query)["uddg"][0]
                except Exception:
                    pass
            results.append({
                "title": link.get_text(strip=True),
                "url": href,
                "snippet": snippet.get_text(strip=True)[:300] if snippet else "",
            })
            if len(results) >= num:
                break

        if not results:
            return {"results": [], "note": "Search ran but no results parsed."}
        return {"results": results}


registry.register(WebFetchTool())
registry.register(WebSearchTool())
