"""User-configurable pre/post tool hooks from <workspace>/.agent/settings.json.

Shape (all keys optional):

    {
      "hooks": {
        "pre_tool":  [{"tools": ["bash", "write"], "run": "<shell command>"}],
        "post_tool": [{"tools": "*",               "run": "<shell command>"}]
      }
    }

Every matched command runs through the system shell with extra env vars:
TOOL_NAME, TOOL_ARGS_JSON, SESSION_ID (+ TOOL_RESULT_JSON / TOOL_STATUS on
post). A pre hook that exits non-zero VETOES the tool call — the model receives
a structured block result with the hook's stderr. Post-hook failures are only
logged. Missing/invalid config means "no hooks", never a crash.
"""
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

HOOK_TIMEOUT = 15
ENV_PAYLOAD_LIMIT = 8000  # chars of TOOL_RESULT_JSON handed to post hooks


@dataclass
class HookRule:
    run: str
    tools: set[str] | None = None  # None/"*" -> all tools

    def matches(self, tool_name: str) -> bool:
        return self.tools is None or tool_name in self.tools


@dataclass
class HookRunner:
    pre_rules: list[HookRule] = field(default_factory=list)
    post_rules: list[HookRule] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path | None = None) -> "HookRunner":
        path = (root or Path.cwd()) / ".agent" / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            hooks = (raw or {}).get("hooks") or {}
            return cls(
                pre_rules=cls._parse(hooks.get("pre_tool")),
                post_rules=cls._parse(hooks.get("post_tool")),
            )
        except FileNotFoundError:
            return cls()
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"hooks config unreadable ({path}): {e}")
            return cls()

    @staticmethod
    def _parse(rules) -> list[HookRule]:
        parsed = []
        for rule in rules or []:
            if not isinstance(rule, dict) or not rule.get("run"):
                continue
            tools = rule.get("tools")
            if tools in ("*", None):
                tool_set = None
            elif isinstance(tools, list):
                tool_set = {str(t) for t in tools}
            else:
                tool_set = {str(tools)}
            parsed.append(HookRule(run=str(rule["run"]), tools=tool_set))
        return parsed

    def _env(self, tool_name: str, arguments: dict, context: dict,
             result: dict | None = None, status: str | None = None) -> dict:
        import os

        env = dict(os.environ)
        args_json = json.dumps(arguments, default=str)
        if len(args_json) > ENV_PAYLOAD_LIMIT:
            args_json = args_json[:ENV_PAYLOAD_LIMIT]
        env.update({
            "TOOL_NAME": tool_name,
            "TOOL_ARGS_JSON": args_json,
            "SESSION_ID": str(context.get("session_id", "")),
        })
        if result is not None:
            result_json = json.dumps(result, default=str)[:ENV_PAYLOAD_LIMIT]
            env["TOOL_RESULT_JSON"] = result_json
            env["TOOL_STATUS"] = status or ""
        return env

    async def _execute(self, rule: HookRule, env: dict) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                rule.run,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=HOOK_TIMEOUT)
        except TimeoutError:
            logger.warning(f"hook timed out after {HOOK_TIMEOUT}s: {rule.run}")
            return 124, "hook timeout"
        except Exception as e:
            logger.warning(f"hook could not start ({rule.run}): {e}")
            return 127, str(e)
        return proc.returncode or 0, (stderr or b"").decode("utf-8", errors="replace")[:500]

    async def run_pre(self, tool_name: str, arguments: dict, context: dict) -> str | None:
        """Return a veto reason when a matching pre hook fails; else None."""
        for rule in self.pre_rules:
            if not rule.matches(tool_name):
                continue
            code, stderr = await self._execute(rule, self._env(tool_name, arguments, context))
            if code != 0:
                reason = f"blocked by pre-tool hook (exit {code}): {stderr.strip() or rule.run}"
                logger.info(reason)
                return reason
        return None

    async def run_post(self, tool_name: str, arguments: dict, context: dict,
                       result: dict, status: str) -> None:
        for rule in self.post_rules:
            if not rule.matches(tool_name):
                continue
            code, stderr = await self._execute(rule, self._env(
                tool_name, arguments, context, result=result, status=status,
            ))
            if code != 0:
                logger.warning(f"post-tool hook failed (exit {code}): "
                               f"{stderr.strip() or rule.run}")
