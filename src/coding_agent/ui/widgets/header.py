"""Top header: identity, model, connection, project, context, host telemetry.

One Static re-rendered from a small dict — cheap enough for a 1s clock timer
plus event-driven updates, and far simpler to keep aligned than a dozen
child widgets.
"""
from datetime import datetime

from rich.text import Text
from textual.widget import Widget

from coding_agent.ui import theme as T


class NexusHeader(Widget):
    DEFAULT_CSS = f"""
    NexusHeader {{
        height: 3;
        dock: top;
        background: {T.SURFACE};
        border-bottom: heavy {T.BORDER};
        padding: 0 1;
    }}
    """

    def __init__(self):
        super().__init__()
        self._info = {
            "version": "0.1.0",
            "model": "—",
            "provider": "—",
            "conn": "BOOT",       # BOOT | READY | LIVE | ERR | OFFLINE
            "project": "—",
            "ctx_pct": 0.0,
            "ctx_label": "—",
            "cpu": None,
            "mem": None,
        }
        self.set_interval(1.0, self.refresh)

    # ---------------------------------------------------------------- update

    def update_info(self, **kw) -> None:
        self._info.update(kw)
        self.refresh()

    # ---------------------------------------------------------------- render

    def render(self) -> Text:
        i = self._info
        line = Text()
        line.append("◆ ", style=f"bold {T.CYAN}")
        line.append("NEXUS", style=f"bold {T.TEXT}")
        line.append(" coding-agent", style=T.MUTED)
        line.append(f" v{i['version']}", style=T.FAINT)
        line.append("   │   ", style=T.FAINT)

        line.append("MODEL ", style=T.MUTED)
        line.append(str(i["model"]), style=f"{T.VIOLET}")

        line.append("   │   ", style=T.FAINT)
        glyph, color = {
            "BOOT": ("◌", T.FAINT), "READY": ("●", T.GREEN),
            "LIVE": ("●", T.CYAN), "ERR": ("●", T.RED),
            "OFFLINE": ("○", T.MUTED),
        }.get(i["conn"], ("◌", T.FAINT))
        line.append(f"{glyph} ", style=color)
        line.append(str(i["conn"]), style=color)

        line.append("   │   ", style=T.FAINT)
        line.append("PROJ ", style=T.MUTED)
        line.append(str(i["project"]), style=T.BLUE)

        line.append("   │   ", style=T.FAINT)
        line.append("CTX ", style=T.MUTED)
        pct = float(i["ctx_pct"] or 0.0)
        color = T.CYAN if pct < 0.5 else (T.AMBER if pct < 0.75 else T.RED)
        blocks = round(pct * 10)
        line.append("▰" * blocks + "▱" * (10 - blocks), style=color)
        line.append(f" {str(i['ctx_label'])}", style=color)

        if i.get("cpu") is not None:
            line.append("   │   ", style=T.FAINT)
            line.append("CPU ", style=T.MUTED)
            line.append(f"{i['cpu']:.0f}%", style=T.TEXT)
            line.append(" MEM ", style=T.MUTED)
            line.append(str(i["mem"] or "—"), style=T.TEXT)

        line.append("   │   ", style=T.FAINT)
        line.append(datetime.now().strftime("%H:%M:%S"), style=T.MUTED)
        return line
