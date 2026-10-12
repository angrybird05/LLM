"""Live validation of Ask 8: one small Anthropic call generating a
conventional-commit message from a toy diff."""
import asyncio
import os
import sys

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "src")))

from coding_agent.core.llm.factory import get_provider  # noqa: E402
from coding_agent.services.git_service import generate_commit_message  # noqa: E402

STAT = """ services/git_service.py | 42 ++++++++++++++++++
 src/coding_agent/tui/app.py        | 18 ++++++++
 2 files changed, 60 insertions(+)"""

EXCERPT = """diff --git a/src/coding_agent/services/git_service.py b/src/coding_agent/services/git_service.py
+class CheckpointStore:
+    \"\"\"LIFO stack of agent file changes; /undo pops the last one.\"\"\"
+    def record(self, path, before, action):
+        self.records.append(ChangeRecord(path=path, before=before, action=action))"""


async def main():
    provider = get_provider("anthropic")
    msg = await generate_commit_message(provider, STAT, EXCERPT)
    print(f"generated commit subject: {msg!r}")
    assert msg, "expected a non-empty subject"
    assert len(msg) <= 72, f"subject too long: {len(msg)}"
    kinds = ("feat", "fix", "refactor", "chore", "docs", "test", "style")
    assert msg.startswith(kinds), f"not conventional-commit style: {msg}"
    assert "checkpoint" in msg.lower() or "undo" in msg.lower(), \
        f"subject should reflect the diff content: {msg}"
    print("\nLIVE ASK 8 CHECK PASSED")


asyncio.run(main())
