"""Offline verification of the chat UI polish: banner, user echo, spinner
gating, context-pressure status line, silent empty turns, tool summaries.
Renders through a forced non-interactive console so pipes/smokes stay clean."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import rich.console  # noqa: E402
from rich.text import Text  # noqa: E402

from coding_agent.tui import renderer  # noqa: E402

# Deterministic headless rendering (no ANSI, is_interactive=False even on a TTY)
renderer.console = rich.console.Console(force_interactive=False)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def grab(fn, *args, **kwargs):
    with renderer.console.capture() as cap:
        fn(*args, **kwargs)
    return _ANSI.sub("", cap.get())


# ---- 1. banner: compact, tilde-abbreviated, tips line -------------------------
out = grab(renderer.banner, "0.1.0", "claude-sonnet-5", "anthropic", "default",
           str(Path.home()))
assert "coding-agent" in out and "v0.1.0" in out
assert "~" in out and str(Path.home()) not in out, "home path should be abbreviated"
assert "/help" in out and "history" in out
long_ws = str(Path.home() / "deep" / "nested" / "folder-structure" / "jobs"
              / "6184d4f6abc" / "temporary" / "ui_smoke_ws")
out = grab(renderer.banner, "0.1.0", "claude-sonnet-5", "anthropic", "default", long_ws)
assert "…" in out and long_ws not in out, "long paths must collapse to ~\\first\\…\\last"
assert all(len(line) <= 80 for line in out.splitlines()), "banner must fit 80 cols"
print("PASS banner: compact greeting, ~ home, middle-collapsed long path, hint line")

# ---- 2. echo_user: bold quote, continuation marker, markup escaped ------------
out = grab(renderer.echo_user, "fix [the] login bug\nsecond line")
assert "›" in out and "fix [the] login bug" in out, repr(out)
assert "second line" in out
assert out.count("fix") == 1
print("PASS echo_user: prompt glyph, multi-line continuation, brackets survive")

# ---- 3. spinner: skipped when non-interactive, safe start/stop ----------------
sp = renderer.Spinner()
sp.maybe_start()
assert sp._thread is None, "non-interactive consoles must never spin"
sp.stop()  # no-op path
print("PASS spinner disabled for pipes/non-TTY (smoke output stays clean)")

# ---- 3b. spinner race: stop->start churn must not resurrect old spinners ------
import io  # noqa: E402
import time as _time  # noqa: E402

started = []


class FakeStatus:
    def __init__(self, *a, **k):
        started.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def update(self, s):
        pass


racing_console = rich.console.Console(force_terminal=True, file=io.StringIO())
racing_console.status = FakeStatus  # type: ignore[assignment]
real_console, renderer.console = renderer.console, racing_console
try:
    sp = renderer.Spinner()
    sp.maybe_start()   # generation 1 goes to sleep for DELAY
    sp.stop()          # REPL hits an event immediately...
    sp.maybe_start()   # ...and restarts for the next gap
    _time.sleep(0.55)  # both sleepers wake past the 0.35s delay
    assert len(started) == 1, \
        f"stale spinner resurrection: {len(started)} statuses started"
    sp.stop()
finally:
    renderer.console = real_console
print("PASS spinner race: rapid stop/start leaves exactly one live status")

# ---- 4. status_line: token counts + context pressure colors -------------------
from coding_agent.config import settings  # noqa: E402

out = grab(renderer.status_line, {"input": 1200, "output": 340}, 3, 4.2,
           context_tokens=20_000)
assert "↑1,200 ↓340 tokens" in out and "3 steps" in out and "4.2s" in out
assert "ctx" in out
lim = settings.CONTEXT_TOKEN_LIMIT
near_compact = int(lim * settings.COMPACT_THRESHOLD) + 10
out = grab(renderer.status_line, {"input": 1, "output": 1}, 1, 0.1,
           context_tokens=near_compact)
assert f"ctx {min(int(100 * near_compact / lim), 100)}%" in out
low = grab(renderer.status_line, {"input": 1, "output": 1}, 1, 0.1,
           context_tokens=lim // 100)
print("PASS status_line: usage/steps/time + ctx % with compact-threshold zone")

# ---- 5. empty turn prints nothing, real content still renders -----------------
st = renderer.StreamingText()
with renderer.console.capture() as cap:
    st.start()
    st.append("   \n")
    st.finish()
assert cap.get().strip() == "", repr(cap.get())
st = renderer.StreamingText()
with renderer.console.capture() as cap:
    st.start()
    st.append("**hi** there")
    st.finish()
assert "hi" in cap.get() and "there" in cap.get()
print("PASS streaming: whitespace-only turns stay silent; text renders")
st = renderer.StreamingText()
with renderer.console.capture() as cap:
    st.start()
    st.append("Hi")
    st.append(" there, nice to meet you!")
    st.finish()
assert cap.get().count("Hi") == 1 and "meet you" in cap.get(), \
    repr(cap.get())  # pipes get ONE paint — no partial Live snapshots
print("PASS streaming: non-interactive consoles paint the message exactly once")

# ---- 5b. segments between tool calls must not replay earlier narration --------
# The REPL reuses one StreamingText for every text segment of a turn; finish()
# resets the buffer so segment 2 never re-prints segment 1 (the hotfix).
st = renderer.StreamingText()
with renderer.console.capture() as cap1:
    st.start()
    st.append("narration before tools")
    st.finish()
with renderer.console.capture() as cap2:
    st.append("final answer after tools")
    st.finish()
assert "narration before tools" in cap1.get(), repr(cap1.get())
assert "narration" not in cap2.get(), f"segment replayed: {cap2.get()!r}"
assert "final answer" in cap2.get()
with renderer.console.capture() as cap3:
    st.finish()          # idempotent: nothing left in the buffer
    st.finish()
assert cap3.get().strip() == "", repr(cap3.get())
print("PASS streaming segments: no cross-segment replay; finish is idempotent")

# ---- 5c. interactive preview stays a bounded tail (no whole-doc repaint) -------
class FakeLive:
    def __init__(self):
        self.frames = []

    def update(self, renderable):
        self.frames.append(renderable)


def _frame_text(frame):
    if isinstance(frame, Text):
        return frame.plain
    return "\n".join(r.plain for r in frame.renderables if isinstance(r, Text))


i_console = rich.console.Console(force_terminal=True, force_interactive=True)
real_console, renderer.console = renderer.console, i_console
try:
    st = renderer.StreamingText()
    fake = FakeLive()
    st._live = fake  # seam: exercise the preview painter without Live startup
    long_text = "\n".join(f"line {i}" for i in range(20))
    st.append(long_text)
    st._paint(force=True)
    assert fake.frames, "interactive append must repaint the preview"
    body = _frame_text(fake.frames[-1])
    assert "…" in body, "clipped preview needs a head marker"
    content_lines = [ln for ln in body.splitlines() if ln != "…"]
    assert len(content_lines) <= renderer.StreamingText.PREVIEW_LINES, \
        f"preview must stay bounded, got {len(content_lines)} lines"
    assert content_lines[-1] == "line 19", "preview must show the newest tail"
finally:
    renderer.console = real_console
print("PASS streaming preview: bounded tail with head marker, newest line last")

# ---- 6. tool renderings -------------------------------------------------------
out = grab(renderer.tool_start, "bash", {"command": "pytest -q"})
assert "●" in out and "bash" in out and "pytest -q" in out
out = grab(renderer.tool_repeat, "read", {"path": "README.md"})
assert "↻" in out and "read(README.md)" in out, repr(out)
out = grab(renderer.tool_result, {"declined": True}, {})
assert "declined" in out
out = grab(renderer.tool_result, {"error": "boom"}, {})
assert "error" in out and "boom" in out
out = grab(renderer.tool_result,
           {"stdout": "\n".join(f"line{i}" for i in range(40)), "returncode": 0},
           {"command": "x"})
assert "line11" in out and "line39" not in out, "output should clip at 12 lines"
out = grab(renderer.tool_result, {"stdout": "", "stderr": "bad", "returncode": 2}, {})
assert "exit code 2" in out and "bad" in out
print("PASS tool lines: call briefs, decline/error marks, clipped stdout, exit codes")

print("\nALL UI CHECKS PASSED")
