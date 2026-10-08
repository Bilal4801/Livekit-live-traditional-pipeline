from __future__ import annotations

import threading

from src.services.call_session import CallSession


class SessionStore:
    """Thread-safe map of caller phone number → CallSession."""

    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}
        self._by_room: dict[str, CallSession] = {}
        self._lock = threading.RLock()

    def put(self, session: CallSession) -> None:
        with self._lock:
            self._sessions[session.from_number] = session
            self._by_room[session.room_name] = session

    def get(self, from_number: str) -> CallSession | None:
        with self._lock:
            return self._sessions.get(from_number)

    def get_by_room(self, room_name: str) -> CallSession | None:
        with self._lock:
            return self._by_room.get(room_name)

    def delete(self, from_number: str) -> None:
        with self._lock:
            session = self._sessions.pop(from_number, None)
            if session is not None:
                self._by_room.pop(session.room_name, None)


# Process-wide store for the FastAPI middleware
session_store = SessionStore()
