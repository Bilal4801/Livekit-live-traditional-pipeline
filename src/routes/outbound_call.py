from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, Request, Response
from twilio.twiml.voice_response import VoiceResponse

from src.config import Settings
from src.security import verify_twilio_signature

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/outbound-call", dependencies=[Depends(verify_twilio_signature)])
async def outbound_call(
    request: Request,
    Caller: str = Form(default="", max_length=64),
) -> Response:
    """Webhook for the Flex/agent-facing phone number — enqueue to TaskRouter."""
    settings: Settings = request.app.state.settings
    response = VoiceResponse()

    if not settings.twilio_flex_workflow_sid:
        logger.error("TWILIO_FLEX_WORKFLOW_SID is required when Flex mode is enabled")
        response.say("Sorry, the contact center is not configured.")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    logger.info("Enqueueing Flex task for caller=%s", Caller)
    response.say("A customer is on the line.")
    enqueue = response.enqueue(workflow_sid=settings.twilio_flex_workflow_sid)
    enqueue.task(
        json.dumps({"name": Caller or "unknown", "type": "inbound", "from": Caller})
    )
    return Response(content=str(response), media_type="application/xml")
