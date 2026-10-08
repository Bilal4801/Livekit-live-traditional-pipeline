from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from src import caller_messages
from src.config import Settings
from src.security import verify_twilio_signature
from src.services.session_store import session_store
from src.services.twilio_livekit import TwilioLiveKitBridge

logger = logging.getLogger(__name__)
router = APIRouter()


def _restore_plus(value: str) -> str:
    """'+' in a query string arrives as a space. Put the plus back."""
    return value.replace(" ", "+")


@router.post("/agent-answered", dependencies=[Depends(verify_twilio_signature)])
async def agent_answered(
    request: Request,
    AnsweredBy: str | None = Form(default=None, max_length=64),
    from_: str = Query(..., alias="from", max_length=64),
    room: str = Query(..., pattern=r"^call-CA[0-9a-fA-F]{32}$"),
) -> Response:
    """AMD gate for the agent leg — connect LiveKit outbound stream only for humans."""
    settings: Settings = request.app.state.settings
    bridge: TwilioLiveKitBridge = request.app.state.bridge
    answered_by = AnsweredBy or "unknown"
    from_ = _restore_plus(from_)
    response = VoiceResponse()

    # Only act on calls this server created; identities and SIDs come from the stored session.
    session = session_store.get_by_room(room) or session_store.get(from_)
    if session is None or session.closed:
        logger.error("No CallSession for room=%s on agent-answered", room)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    # machine_start is a greeting or IVR, which a live phone often triggers.
    # Only a finished voicemail or a fax should end the call.
    if answered_by.startswith("machine_end") or answered_by == "fax":
        logger.info("Agent leg answered by %s — not connecting", answered_by)
        response.hangup()

        twilio = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        messages = caller_messages.for_language(session.caller_language)
        caller_response = VoiceResponse()
        caller_response.say(messages.no_agent, voice=messages.voice, language=messages.language)
        caller_response.hangup()
        try:
            twilio.calls(session.caller_call_sid).update(twiml=str(caller_response))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to end caller leg: %s", exc)

        session.closed = True
        session_store.delete(session.from_number)
        await bridge.delete_room(session.room_name)
        return Response(content=str(response), media_type="application/xml")

    logger.info("Agent leg answered by %s — connecting outbound connector", answered_by)
    connect_url = await bridge.connect_outbound(
        room_name=session.room_name,
        participant_identity=session.agent_identity,
        participant_name=f"Agent for {session.from_number}",
        participant_metadata={"role": "agent", "from": session.from_number},
    )

    # Keep room metadata in sync (agent identity already set at create)
    await bridge.update_room_metadata(session.room_name, session.room_metadata())

    response.say("A customer is on the line.")
    connect = Connect()
    stream = Stream(url=connect_url, name="Outbound Audio Stream")
    connect.append(stream)
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")
