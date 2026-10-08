from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from twilio.rest import Client as TwilioClient
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from src import caller_messages
from src.agent import languages
from src.config import Settings
from src.security import verify_twilio_signature
from src.services.call_session import CallSession
from src.services.session_store import session_store
from src.services.twilio_livekit import TwilioLiveKitBridge

logger = logging.getLogger(__name__)
router = APIRouter()


def _twilio_client(settings: Settings) -> TwilioClient:
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)


def _normalize_language(lang: str | None, default: str) -> str:
    key = (lang or "").strip().lower()
    if languages.from_key(key) is not None:
        return key
    return default


@router.post("/incoming-call", dependencies=[Depends(verify_twilio_signature)])
async def incoming_call(
    request: Request,
    From: str = Form(..., max_length=64),
    To: str = Form(..., max_length=64),
    CallSid: str = Form(..., pattern=r"^CA[0-9a-fA-F]{32}$"),
    lang: str | None = Query(default=None, max_length=32),
) -> Response:
    """Studio redirect after language selection — bridge caller into LiveKit."""
    settings: Settings = request.app.state.settings
    bridge: TwilioLiveKitBridge = request.app.state.bridge
    caller_language = _normalize_language(lang, settings.caller_language)
    room_name = f"call-{CallSid}"
    caller_identity = f"caller-{From}"
    agent_identity = f"agent-{From}"

    session = CallSession(
        from_number=From,
        caller_language=caller_language,
        room_name=room_name,
        caller_call_sid=CallSid,
        caller_identity=caller_identity,
        agent_identity=agent_identity,
        extra={
            "skip_flex": settings.skip_flex,
            "to": To,
        },
    )
    # Plain-phone mode starts as soon as the agent leg joins; Flex waits for reservation.
    if settings.skip_flex:
        session.start_requested = True

    await bridge.ensure_room(room_name, session.room_metadata())

    connect_url = await bridge.connect_inbound(
        room_name=room_name,
        participant_identity=caller_identity,
        participant_name=From,
        participant_metadata={"role": "caller", "from": From, "lang": caller_language},
        agent_metadata={
            "from": From,
            "lang": caller_language,
            "caller_identity": caller_identity,
            "agent_identity": agent_identity,
            "caller_call_sid": CallSid,
            "skip_flex": settings.skip_flex,
            "start_requested": session.start_requested,
        },
    )

    session_store.put(session)
    logger.info("Inbound session stored lang=%s room=%s", caller_language, room_name)

    # Dial the human agent leg (Flex number or plain phone)
    twilio = _twilio_client(settings)
    # '+' in a phone number must be percent-encoded or Twilio decodes it as a space.
    query = urlencode({"from": From, "room": room_name})
    agent_call = twilio.calls.create(
        from_=settings.twilio_caller_number,
        to=settings.twilio_agent_number,
        caller_id=From,
        url=f"{settings.public_https_base}/agent-answered?{query}",
    )
    session.agent_call_sid = agent_call.sid
    session_store.put(session)
    logger.info("Dialed agent leg sid=%s", agent_call.sid)

    messages = caller_messages.for_language(caller_language)
    response = VoiceResponse()
    response.say(messages.please_wait, voice=messages.voice, language=messages.language)
    connect = Connect()
    stream = Stream(url=connect_url, name="Caller Audio Stream")
    connect.append(stream)
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")
