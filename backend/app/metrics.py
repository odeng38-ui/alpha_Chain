"""Small dependency-free Prometheus metrics registry for HTTP requests."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class RequestMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._count: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration: dict[tuple[str, str], float] = defaultdict(float)

    def observe(self, method: str, path: str, status: int, duration_seconds: float) -> None:
        method = method.upper()
        with self._lock:
            self._count[(method, path, status)] += 1
            self._duration[(method, path)] += duration_seconds

    def render(self) -> str:
        with self._lock:
            counts = sorted(self._count.items())
            durations = sorted(self._duration.items())

        lines = [
            "# HELP alpha_chain_http_requests_total Total HTTP requests.",
            "# TYPE alpha_chain_http_requests_total counter",
        ]
        for (method, path, status), value in counts:
            labels = f'method="{method}",path="{path}",status="{status}"'
            lines.append(f"alpha_chain_http_requests_total{{{labels}}} {value}")
        lines.extend([
            "# HELP alpha_chain_http_request_duration_seconds_total Total request duration.",
            "# TYPE alpha_chain_http_request_duration_seconds_total counter",
        ])
        for (method, path), value in durations:
            labels = f'method="{method}",path="{path}"'
            lines.append(f"alpha_chain_http_request_duration_seconds_total{{{labels}}} {value:.6f}")
        return "\n".join(lines) + "\n"


request_metrics = RequestMetrics()
