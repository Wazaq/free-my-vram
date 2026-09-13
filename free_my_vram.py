#!/usr/bin/env python3
"""Free My VRAM: release ComfyUI models after an idle period.

Uses only ComfyUI's HTTP API and the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8188"
DEFAULT_IDLE_MINUTES = 10.0
DEFAULT_CHECK_SECONDS = 60.0
HTTP_TIMEOUT_SECONDS = 5.0


class ComfyAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueueState:
    running: int
    pending: int

    @property
    def busy(self) -> bool:
        return self.running > 0 or self.pending > 0


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


class ComfyClient:
    def __init__(self, base_url: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
    ) -> Any:
        body = None
        headers: dict[str, str] = {}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyAPIError(str(exc)) from exc

        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    def queue_state(self) -> QueueState:
        data = self._request("/queue")
        if not isinstance(data, dict):
            raise ComfyAPIError("Unexpected response from /queue")
        return QueueState(
            running=len(data.get("queue_running") or []),
            pending=len(data.get("queue_pending") or []),
        )

    def history(self) -> dict[str, Any]:
        data = self._request("/history?max_items=1")
        return data if isinstance(data, dict) else {}

    def free_memory(self) -> None:
        self._request(
            "/free",
            method="POST",
            data={"unload_models": True, "free_memory": True},
        )


def _timestamp_seconds(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    # ComfyUI message timestamps are normally milliseconds since epoch.
    if value > 10_000_000_000:
        return float(value) / 1000.0
    return float(value)


def most_recent_history_timestamp(history: dict[str, Any]) -> float | None:
    """Return the newest status timestamp in the newest history entry.

    ComfyUI versions/custom builds can vary in their terminal status event names.
    For example, ComfyUI 0.34.2 uses execution_success, execution_error, or
    execution_interrupted rather than execution_end. Taking the newest timestamp
    from the status messages keeps the idle clock tied to the terminal event
    without hard-coding one version-specific event name.
    """
    newest: float | None = None

    for job in history.values():
        if not isinstance(job, dict):
            continue
        status = job.get("status")
        if not isinstance(status, dict):
            continue
        messages = status.get("messages") or []
        for message in messages:
            if not isinstance(message, (list, tuple)) or len(message) < 2:
                continue
            payload = message[1]
            if not isinstance(payload, dict):
                continue
            timestamp = _timestamp_seconds(payload.get("timestamp"))
            if timestamp is not None and (newest is None or timestamp > newest):
                newest = timestamp

    return newest


def check_once(
    client: ComfyClient,
    *,
    idle_seconds: float,
    dry_run: bool,
    free_now: bool,
    last_freed_job_timestamp: float | None,
) -> float | None:
    """Perform one conservative check.

    Returns the history timestamp associated with the last unload so a continuous
    process does not repeatedly send /free for the same completed job.
    """
    try:
        queue = client.queue_state()
    except ComfyAPIError as exc:
        log(f"ComfyUI not reachable: {exc}")
        return last_freed_job_timestamp

    if queue.busy:
        log(
            f"ComfyUI busy ({queue.running} running, {queue.pending} pending) "
            "- leaving it alone."
        )
        return last_freed_job_timestamp

    try:
        history = client.history()
    except ComfyAPIError as exc:
        log(f"Could not read ComfyUI history: {exc}")
        return last_freed_job_timestamp

    job_timestamp = most_recent_history_timestamp(history)
    now = time.time()

    if not free_now:
        if job_timestamp is None:
            log("Queue is empty, but no usable completion timestamp was found - skipping safely.")
            return last_freed_job_timestamp

        idle_for = max(0.0, now - job_timestamp)
        if idle_for < idle_seconds:
            log(
                f"ComfyUI idle for {int(idle_for)}s "
                f"(threshold {int(idle_seconds)}s) - not unloading yet."
            )
            return last_freed_job_timestamp

        if last_freed_job_timestamp == job_timestamp:
            log("No new ComfyUI job since the last unload - nothing to do.")
            return last_freed_job_timestamp

        log(
            f"ComfyUI idle for {int(idle_for)}s "
            f"(threshold {int(idle_seconds)}s)."
        )
    else:
        log("Queue is empty; manual free-now request accepted.")

    if dry_run:
        log("Dry run: would request model unload and memory release.")
        return job_timestamp if job_timestamp is not None else last_freed_job_timestamp

    log("Requesting model unload and memory release...")
    try:
        client.free_memory()
    except ComfyAPIError as exc:
        log(f"Free-memory request failed: {exc}")
        return last_freed_job_timestamp

    log("Done. ComfyUI accepted the free-memory request.")
    return job_timestamp if job_timestamp is not None else last_freed_job_timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Release ComfyUI models after an idle period without editing workflows."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"ComfyUI base URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--idle-minutes",
        type=float,
        default=DEFAULT_IDLE_MINUTES,
        help=f"Minutes of inactivity before unloading (default: {DEFAULT_IDLE_MINUTES:g})",
    )
    parser.add_argument(
        "--check-seconds",
        type=float,
        default=DEFAULT_CHECK_SECONDS,
        help=f"Seconds between checks in watch mode (default: {DEFAULT_CHECK_SECONDS:g})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit instead of watching continuously",
    )
    parser.add_argument(
        "--free-now",
        action="store_true",
        help="Free memory immediately if the ComfyUI queue is empty",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without sending the /free request",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.idle_minutes < 0:
        parser.error("--idle-minutes must be zero or greater")
    if args.check_seconds <= 0:
        parser.error("--check-seconds must be greater than zero")

    client = ComfyClient(args.url)
    idle_seconds = args.idle_minutes * 60.0
    last_freed_job_timestamp: float | None = None

    if args.once or args.free_now:
        check_once(
            client,
            idle_seconds=idle_seconds,
            dry_run=args.dry_run,
            free_now=args.free_now,
            last_freed_job_timestamp=last_freed_job_timestamp,
        )
        return 0

    log(
        f"Watching {client.base_url}; unload after {args.idle_minutes:g} idle minute(s); "
        f"checking every {args.check_seconds:g}s."
    )

    try:
        while True:
            last_freed_job_timestamp = check_once(
                client,
                idle_seconds=idle_seconds,
                dry_run=args.dry_run,
                free_now=False,
                last_freed_job_timestamp=last_freed_job_timestamp,
            )
            time.sleep(args.check_seconds)
    except KeyboardInterrupt:
        log("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
