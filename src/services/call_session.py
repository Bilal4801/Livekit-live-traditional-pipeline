from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallSession:
    """In-memory state for one caller↔agent translation call (FastAPI process)."""

    from_number: str
    caller_language: str
    room_name: str
    caller_call_sid: str
    caller_identity: str
    agent_identity: str
    agent_call_sid: str | None = None
    start_requested: bool = False
    closed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def room_metadata(self) -> str:
        return json.dumps(
            {
                "from": self.from_number,
                "lang": self.caller_language,
                "caller_identity": self.caller_identity,
                "agent_identity": self.agent_identity,
                "caller_call_sid": self.caller_call_sid,
                "start_requested": self.start_requested,
                "skip_flex": self.extra.get("skip_flex", False),
            }
        )
