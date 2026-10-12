"""SystemPanel: the right column - context, tokens, latency, host telemetry.

Data rows update from turn events via the TokenTracker; host rows (CPU / MEM
/ GPU) update from a 2s psutil sampler. Unavailable data renders as a dim
dash instead of an error.
"""
from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Static

from coding_agent.ui import theme as T


def _bar(pct: float, width: int = 8) -> str:
    pct = max(0.0, min(1.0, pct))
    return chr(9646) * round(pct * width) + chr(9647) * (width - round(pct * width))


class SystemPanel(Vertical):
    DEFAULT_CSS = """
    SystemPanel {
        width: 30;
        min-width: 26;
        border: round #1a2942;
        background: #0b1220;
        padding: 0 1;
    }
    .sysrow {
        height: auto;
        color: #5d7290;
    }
    """

    ROWS = [
        ("CONTEXT", "ctx"), ("TOKENS", "tok"), ("LAST TURN", "last"),
        ("LATENCY", "lat"), ("TOOLS", "tools"), ("API", "api"),
        ("MODEL", "model"), ("CPU", "cpu"), ("MEM", "mem"), ("GPU", "gpu"),
    ]

    def __init__(self, tracker, metrics):
        super().__init__()
        self.tracker = tracker
        self.metrics = metrics
        self.api_glyph = "-"
        self.api_color = "#31435e"

    def compose(self):
        yield Static(Text("SYSTEM", style="bold #5d7290"), id="stitle")
        for _label, id_ in self.ROWS:
            yield Static("", id=id_, classes="sysrow")

    def set_row(self, id_: str, line: Text) -> None:
        try:
            self.query_one("#" + id_, Static).update(line)
        except Exception:
            pass

    def set_api_status(self, state: str, color: str) -> None:
        self.api_glyph = state
        self.api_color = color
        self.refresh_data_rows()

    def refresh_data_rows(self) -> None:
        """Rows driven by the token tracker (turn events)."""
        from coding_agent.ui.animations import bar

        t = self.tracker
        limit = getattr(self.app, "context_token_limit", 0)
        pct = t.context_pct(limit) if limit else 0.0
        color = T.CYAN if pct < 0.5 else (T.AMBER if pct < 0.75 else T.RED)

        ctx = Text()
        ctx.append(" CONTEXT ", style="bold #5d7290")
        if t.context_tokens is not None:
            ctx.append(bar(pct, 10), style=color)
            ctx.append(f" ~{t.context_tokens:,}".replace(",", " "), style="#c7d5e8")
            ctx.append(f" {pct * 100:.0f}%", style=color)
        else:
            ctx.append("no turn yet", style="#31435e")
        self.set_row("ctx", ctx)

        tok = Text()
        tok.append(" TOKENS  ", style="bold #5d7290")
        tok.append(f"in {t.input_tokens:,}  out {t.output_tokens:,}", style="#c7d5e8")
        if getattr(self.app, "busy", False) and (t.turn_input or t.turn_output):
            tok.append(f"  · turn ↑{t.turn_input:,} ↓{t.turn_output:,}",
                       style=T.AMBER)
        self.set_row("tok", tok)

        last = Text()
        last.append(" LAST TURN ", style="bold #5d7290")
        if t.last_turn_s is not None:
            ttft = f" · first {t.last_ttft:.1f}s" if t.last_ttft is not None else ""
            iters = f" · {t.last_iterations} steps" if t.last_iterations else ""
            rate = (f" · {t.last_out_rate:.0f} tok/s"
                    if getattr(t, "last_out_rate", None) else "")
            last.append(f"{t.last_turn_s:.1f}s{ttft}{iters}{rate}", style="#c7d5e8")
        else:
            last.append("-", style="#31435e")
        self.set_row("last", last)

        lat = Text()
        lat.append(" LATENCY  ", style="bold #5d7290")
        avg = t.avg_ttft
        if avg is not None:
            lat.append(f"{avg:.2f}s avg to first byte", style="#c7d5e8")
        else:
            lat.append("-", style="#31435e")
        self.set_row("lat", lat)

        tools = Text()
        tools.append(" TOOLS    ", style="bold #5d7290")
        tools.append(f"{t.tool_calls} calls", style="#c7d5e8")
        if t.tools_active:
            tools.append(f" - {t.tools_active} running", style="#fbbf24")
        self.set_row("tools", tools)

        api = Text()
        api.append(" API      ", style="bold #5d7290")
        api.append(self.api_glyph + " ", style=f"bold {self.api_color}")
        label = str(getattr(self.app, "provider_label", "") or "-")
        api.append(label, style="#c7d5e8")
        self.set_row("api", api)

        model = Text()
        model.append(" MODEL    ", style="bold #5d7290")
        model.append(str(getattr(self.app, "model", None) or "-"), style="#a78bfa")
        self.set_row("model", model)

    def refresh_host_rows(self) -> None:
        """CPU / MEM / GPU rows, sampled on a timer."""
        s = self.metrics.sample()

        cpu = Text()
        cpu.append(" CPU      ", style="bold #5d7290")
        cpu_val = float(s["cpu"])
        cpu.append(_bar(cpu_val / 100.0), style=T.CYAN)
        cpu.append(f" {cpu_val:.0f}%", style="#c7d5e8")
        self.set_row("cpu", cpu)

        mem = Text()
        mem.append(" MEM      ", style="bold #5d7290")
        mem.append(_bar(float(s["mem_pct"]) / 100.0), style=T.BLUE)
        mem.append(f" {s['mem_used']}/{s['mem_total']}", style="#c7d5e8")
        self.set_row("mem", mem)

        gpu = Text()
        gpu.append(" GPU      ", style="bold #5d7290")
        info = self.metrics.probe_gpu()
        if info is not None:
            gpu.append(_bar(info.util_pct / 100.0), style=T.GREEN)
            gpu.append(
                f" {info.util_pct:.0f}%  {info.mem_used_mb}/{info.mem_total_mb}M",
                style="#c7d5e8",
            )
            gpu.append(f"  {info.name[:12]}", style="#31435e")
        else:
            gpu.append("n/a (integrated)", style="#31435e")
        self.set_row("gpu", gpu)
