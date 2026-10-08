from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, Request, Response

from src.security import verify_twilio_signature
from src.services.session_store import session_store
from src.services.twilio_livekit import TwilioLiveKitBridge

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/reservation-accepted", dependencies=[Depends(verify_twilio_signature)])
async def reservation_accepted(
    request: Request,
    EventType: str = Form(..., max_length=128),
    TaskAttributes: str = Form(default="{}", max_length=16384),
) -> Response:
    """TaskRouter event — arm translation once a Flex agent accepts the task."""
    bridge: TwilioLiveKitBridge = request.app.state.bridge

    if EventType != "reservation.accepted":
        return Response(content="OK", status_code=200)

    try:
        attrs = json.loads(TaskAttributes)
    except json.JSONDecodeError:
        logger.error("Invalid TaskAttributes JSON")
        return Response(content="Bad Request", status_code=400)
    if not isinstance(attrs, dict):
        return Response(content="Bad Request", status_code=400)

    from_number = attrs.get("from") or attrs.get("name")
    if not from_number:
        logger.error("No from/name in TaskAttributes")
        return Response(content="Not Found", status_code=404)

    session = session_store.get(str(from_number))
    if session is None:
        logger.error("CallSession not found for reservation")
        return Response(content="Not Found", status_code=404)

    session.start_requested = True
    session_store.put(session)
    await bridge.update_room_metadata(session.room_name, session.room_metadata())
    logger.info("Reservation accepted — start_requested for room=%s", session.room_name)
    return Response(content="OK", status_code=200)
