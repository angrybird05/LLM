"""Offline verification of REPL input polish: multi-line entry and persistent
history. The interactive prompt_toolkit path is driven through pipe input
(real key bindings, no terminal needed); the piped-stdin fallback is tested
with a fake console."""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coding_agent.tui.input_line import PROMPT_HTML, LineReader  # noqa: E402


class FakeConsole:
    """console.input stand-in: pops scripted replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def input(self, prompt=""):
        self.prompts.append(prompt)
        if not self.replies:
            raise EOFError
        return self.replies.pop(0)

    def print(self, *args, **kwargs):
        pass  # degradation notice lands here


HIST = Path(tempfile.mkdtemp(prefix="polish_hist_")) / "history"

# ---- 1. fallback path (piped stdin): plain line, EOF -> None ------------------
fc = FakeConsole("/help")
r = LineReader(history_path=HIST, use_prompt_toolkit=False)
assert r.read(fc) == "/help"
assert r.read(FakeConsole()) is None  # immediate EOF
print("PASS fallback reads one line; EOF returns None")

# ---- 2. fallback multi-line: trailing backslash continues ---------------------
fc = FakeConsole("fix the \\", "login bug \\", "today")
out = r.read(fc)
assert out == "fix the \nlogin bug \ntoday", repr(out)
print("PASS fallback joins backslash-continued lines")

# ---- 3. interrupt mid-continuation keeps what was typed -----------------------
fc = FakeConsole("partial \\", "still typing")
out = r.read(fc)
assert out == "partial \nstill typing", repr(out)
print("PASS interrupt during continuation preserves typed text")

# ---- 3b. undrivable terminal: degrade to fallback instead of crashing ---------
r2 = LineReader(history_path=HIST)  # stdin is a TTY here -> tries prompt_toolkit
r2._read_interactive = lambda: (_ for _ in ()).throw(RuntimeError("no console"))
fc = FakeConsole("plain answer")
out = r2.read(fc)
assert out == "plain answer", repr(out)
assert r2._degraded
print("PASS prompt_toolkit failure degrades to simple input, no crash")


# ---- 4. interactive path via pipe input: real bindings + history --------------
def drive(keys, hist=HIST):
    """Start one real prompt against pipe input, give the app a moment to
    render + load history (like a human taking time to type), then feed keys."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    async def run():
        with create_pipe_input() as pipe:
            reader = LineReader(history_path=hist, input=pipe, output=DummyOutput())
            session = reader._get_session()
            fut = asyncio.ensure_future(session.prompt_async(PROMPT_HTML))
            await asyncio.sleep(0.3)
            pipe.send_text(keys)
            return (await fut).strip()

    return asyncio.run(run())


out = drive("hello \\\nworld\r")
assert out == "hello \nworld", repr(out)  # backslash dropped, rest kept verbatim
print("PASS interactive: trailing backslash continues onto a second line")

# literal backslash NOT at the end must survive untouched
out = drive("C:\\path\\to thing" + "\r")
assert out == "C:\\path\\to thing", repr(out)
print("PASS interactive: interior backslashes stay literal")

# ↑ walks history newest-first; two ups reach the earlier multi-line entry
out = drive("\x1b[A\r")
assert out == "C:\\path\\to thing", repr(out)
out = drive("\x1b[A\x1b[A\r")
assert out == "hello \nworld", repr(out)
print("PASS interactive: up-arrow walks history, replays multi-line entry intact")

# history file exists in FileHistory format (escaped '+' lines for newlines)
raw = HIST.read_text(encoding="utf-8")
assert "+hello" in raw and "world" in raw, raw[:200]
print("PASS history persisted to disk in FileHistory format")

# ---- 5. fresh reader, same file: history survives across launches -------------
hist2 = HIST.with_suffix(".2")
drive("first command\r", hist=hist2)
out = drive("\x1b[A\r", hist=hist2)
assert out == "first command", repr(out)
print("PASS history shared across LineReader instances (launches)")


# ---- 6. Ctrl+C / Ctrl+D at the interactive prompt return None -----------------
def drive_none(keys, hist=HIST):
    """drive() variant mirroring _read_interactive's contract: EOF/Ctrl+C -> None."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    async def run():
        with create_pipe_input() as pipe:
            reader = LineReader(history_path=hist, input=pipe, output=DummyOutput())
            session = reader._get_session()

            async def read():
                try:
                    return (await session.prompt_async(PROMPT_HTML)).strip()
                except (EOFError, KeyboardInterrupt):
                    return None

            fut = asyncio.ensure_future(read())
            await asyncio.sleep(0.3)
            pipe.send_text(keys)
            try:
                return await fut
            except KeyboardInterrupt:
                return None

    return asyncio.run(run())


for label, key in (("Ctrl+D", "\x04"), ("Ctrl+C", "\x03")):
    res = drive_none(key)
    assert res is None, (label, repr(res))
    print(f"PASS {label} at the prompt exits cleanly (None)")

shutil.rmtree(HIST.parent, ignore_errors=True)
print("\nALL INPUT-POLISH CHECKS PASSED")
