"""System + token telemetry for the right panel and header.

TokenTracker accumulates what the agent layer reports (provider usage, context
estimates, latency). SystemMetrics samples the host (psutil) plus an optional
GPU probe — both degrade gracefully to "n/a" when unavailable.
"""
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass

from loguru import logger


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


@dataclass
class GpuInfo:
    name: str
    util_pct: float
    mem_used_mb: int
    mem_total_mb: int


class TokenTracker:
    """Session-wide token/context/latency accounting, fed by turn events."""

    def __init__(self):
        self._committed_in = 0     # tokens from turns that already finished
        self._committed_out = 0
        self.input_tokens = 0      # effective session total (committed + live)
        self.output_tokens = 0
        self.context_tokens: int | None = None
        self.turn_input = 0        # live counters for the in-flight turn
        self.turn_output = 0
        self.turns = 0
        self.tool_calls = 0
        self.tools_active = 0
        self.last_ttft: float | None = None
        self.last_turn_s: float | None = None
        self.last_out_rate: float | None = None   # output tok/s of the last turn
        self.last_iterations = 0
        self._ttfts: deque[float] = deque(maxlen=20)

    def record_usage(self, input_tokens: int, output_tokens: int,
                     context_tokens: int | None) -> None:
        """Live metering mid-turn: UsageUpdated carries cumulative turn totals."""
        self.turn_input = input_tokens
        self.turn_output = output_tokens
        self.input_tokens = self._committed_in + input_tokens
        self.output_tokens = self._committed_out + output_tokens
        if context_tokens is not None:
            self.context_tokens = context_tokens

    def reset_turn_counters(self) -> None:
        """Zero the live per-turn counters when a new turn starts."""
        self.turn_input = 0
        self.turn_output = 0

    def record_turn(self, usage: dict, context_tokens: int | None,
                    elapsed_s: float, ttft_s: float | None):
        self._committed_in += usage.get("input") or 0
        self._committed_out += usage.get("output") or 0
        self.input_tokens = self._committed_in
        self.output_tokens = self._committed_out
        self.reset_turn_counters()
        if context_tokens is not None:
            self.context_tokens = context_tokens
        self.turns += 1
        self.last_turn_s = elapsed_s
        self.last_out_rate = (usage.get("output") or 0) / elapsed_s \
            if elapsed_s else None
        if ttft_s is not None:
            self.last_ttft = ttft_s
            self._ttfts.append(ttft_s)

    def record_tool_start(self):
        self.tool_calls += 1
        self.tools_active += 1

    def record_tool_end(self):
        self.tools_active = max(0, self.tools_active - 1)

    def reset(self) -> None:
        """Session-wide wipe (/clear): committed + live counters back to zero."""
        self._committed_in = 0
        self._committed_out = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.context_tokens = None
        self.last_out_rate = None
        self.reset_turn_counters()

    @property
    def avg_ttft(self) -> float | None:
        return sum(self._ttfts) / len(self._ttfts) if self._ttfts else None

    def context_pct(self, limit: float) -> float:
        if not self.context_tokens or not limit:
            return 0.0
        return min(1.0, self.context_tokens / limit)


class SystemMetrics:
    """Host telemetry. psutil calls are cheap; the GPU probe runs once."""

    def __init__(self):
        self._gpu_checked = False
        self.gpu: GpuInfo | None = None
        try:
            import psutil

            self._psutil = psutil
            psutil.cpu_percent(interval=None)  # prime the counter
        except Exception:
            self._psutil = None

    @property
    def available(self) -> bool:
        return self._psutil is not None

    def sample(self) -> dict[str, object]:
        if not self._psutil:
            return {"cpu": 0.0, "mem_pct": 0.0, "mem_used": "", "mem_total": ""}
        vm = self._psutil.virtual_memory()
        used_gb = (vm.total - vm.available) / 2**30
        total_gb = vm.total / 2**30
        return {
            "cpu": self._psutil.cpu_percent(interval=None),
            "mem_pct": vm.percent,
            "mem_used": f"{used_gb:.1f}G",
            "mem_total": f"{total_gb:.0f}G",
            "proc_mb": self._psutil.Process().memory_info().rss / 2**20,
        }

    def probe_gpu(self) -> GpuInfo | None:
        """One-shot nvidia-smi probe; caches the answer (absence included)."""
        if self._gpu_checked:
            return self.gpu
        self._gpu_checked = True
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip().splitlines()
            if out:
                name, util, used, total = [c.strip() for c in out[0].split(",")]
                self.gpu = GpuInfo(name, float(util), int(float(used)), int(float(total)))
        except Exception as e:  # noqa: BLE001 — any probe failure means "no GPU data"
            logger.debug(f"gpu probe failed: {e}")
        return self.gpu


__all__ = ["GpuInfo", "SystemMetrics", "TokenTracker", "_fmt_tokens"]
