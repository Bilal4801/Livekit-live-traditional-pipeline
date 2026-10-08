from __future__ import annotations

import json
import logging
from typing import Any

from livekit import api

from src.config import Settings

logger = logging.getLogger(__name__)


class TwilioLiveKitBridge:
    """LiveKit Cloud Twilio Connector helpers + room metadata signaling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> api.LiveKitAPI:
        return api.LiveKitAPI(
            url=self._settings.livekit_url,
            api_key=self._settings.livekit_api_key,
            api_secret=self._settings.livekit_api_secret,
        )

    async def ensure_room(self, room_name: str, metadata: str) -> None:
        lk = self._client()
        try:
            await lk.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    metadata=metadata,
                    empty_timeout=60 * 30,
                )
            )
            logger.info("Created LiveKit room %s", room_name)
        except Exception as exc:  # noqa: BLE001
            # Room may already exist from a retry
            logger.warning("create_room %s: %s — updating metadata", room_name, exc)
            await lk.room.update_room_metadata(
                api.UpdateRoomMetadataRequest(room=room_name, metadata=metadata)
            )
        finally:
            await lk.aclose()

    async def update_room_metadata(self, room_name: str, metadata: str) -> None:
        lk = self._client()
        try:
            await lk.room.update_room_metadata(
                api.UpdateRoomMetadataRequest(room=room_name, metadata=metadata)
            )
        finally:
            await lk.aclose()

    async def connect_inbound(
        self,
        *,
        room_name: str,
        participant_identity: str,
        participant_name: str,
        participant_metadata: dict[str, Any],
        agent_metadata: dict[str, Any],
    ) -> str:
        """Return Twilio Media Streams WebSocket URL (connect_url)."""
        lk = self._client()
        try:
            request = api.ConnectTwilioCallRequest(
                twilio_call_direction=api.ConnectTwilioCallRequest.TWILIO_CALL_DIRECTION_INBOUND,
                room_name=room_name,
                participant_identity=participant_identity,
                participant_name=participant_name,
                participant_metadata=json.dumps(participant_metadata),
            )
            # Optional attributes (supported on LiveKit Cloud connector API)
            try:
                request.participant_attributes["role"] = "caller"
                request.participant_attributes["lang"] = str(agent_metadata.get("lang", ""))
            except Exception:  # noqa: BLE001
                pass

            response = await lk.connector.connect_twilio_call(request)
            logger.info(
                "ConnectTwilioCall INBOUND room=%s identity=%s",
                room_name,
                participant_identity,
            )

            # The connector's own agent dispatch is skipped for pre-created rooms.
            dispatch = await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self._settings.agent_name,
                    room=room_name,
                    metadata=json.dumps(agent_metadata),
                )
            )
            logger.info(
                "Dispatched agent %s to room=%s dispatch_id=%s",
                self._settings.agent_name,
                room_name,
                dispatch.id,
            )
            return response.connect_url
        finally:
            await lk.aclose()

    async def connect_outbound(
        self,
        *,
        room_name: str,
        participant_identity: str,
        participant_name: str,
        participant_metadata: dict[str, Any],
    ) -> str:
        lk = self._client()
        try:
            request = api.ConnectTwilioCallRequest(
                twilio_call_direction=api.ConnectTwilioCallRequest.TWILIO_CALL_DIRECTION_OUTBOUND,
                room_name=room_name,
                participant_identity=participant_identity,
                participant_name=participant_name,
                participant_metadata=json.dumps(participant_metadata),
            )
            try:
                request.participant_attributes["role"] = "agent"
            except Exception:  # noqa: BLE001
                pass

            response = await lk.connector.connect_twilio_call(request)
            logger.info(
                "ConnectTwilioCall OUTBOUND room=%s identity=%s",
                room_name,
                participant_identity,
            )
            return response.connect_url
        finally:
            await lk.aclose()

    async def delete_room(self, room_name: str) -> None:
        lk = self._client()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
            logger.info("Deleted LiveKit room %s", room_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_room %s failed: %s", room_name, exc)
        finally:
            await lk.aclose()

    async def unsubscribe_track(
        self, *, room_name: str, identity: str, track_sid: str
    ) -> None:
        lk = self._client()
        try:
            await lk.room.update_subscriptions(
                api.UpdateSubscriptionsRequest(
                    room=room_name,
                    identity=identity,
                    track_sids=[track_sid],
                    subscribe=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "unsubscribe %s from %s in %s failed: %s",
                identity,
                track_sid,
                room_name,
                exc,
            )
        finally:
            await lk.aclose()
