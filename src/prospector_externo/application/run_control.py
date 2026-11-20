"""Registro de control de corridas en vivo: cancelación cooperativa y eventos SSE."""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any


TERMINAL_EVENT_TYPES = {"run_completed", "run_failed", "run_cancelled"}


class RunControlState:
    """Estado en memoria para coordinar cancelación y stream de eventos de una corrida."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.cancel_event = threading.Event()
        self.finished_event = threading.Event()
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "event": event_type,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload or {},
        }
        with self.condition:
            self.events.append(event)
            if event_type in TERMINAL_EVENT_TYPES:
                self.finished_event.set()
            self.condition.notify_all()
        return event


class RunControlRegistry:
    """Coordina cancelación limpia y distribución de eventos SSE entre hilos/coroutines."""

    def __init__(self, max_retained: int = 100) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunControlState] = {}
        self._max_retained = max_retained

    def register(self, run_id: str) -> RunControlState:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                state = RunControlState(run_id)
                self._runs[run_id] = state
                if len(self._runs) > self._max_retained:
                    oldest_finished = [
                        rid for rid, st in self._runs.items() if st.finished_event.is_set()
                    ]
                    for rid in oldest_finished[: len(self._runs) - self._max_retained]:
                        self._runs.pop(rid, None)
            return state

    def get(self, run_id: str) -> RunControlState | None:
        with self._lock:
            return self._runs.get(run_id)

    def publish(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self.register(run_id)
        return state.publish(event_type, payload)

    def request_cancel(self, run_id: str) -> bool:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None or state.finished_event.is_set():
                return False
            state.cancel_event.set()
        state.publish("cancel_requested", {"message": "Cancelación solicitada por operador"})
        return True

    def is_cancelled(self, run_id: str) -> bool:
        state = self.get(run_id)
        return state.cancel_event.is_set() if state is not None else False


RUN_CONTROL = RunControlRegistry()
