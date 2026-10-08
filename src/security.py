from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from src.config import Settings

logger = logging.getLogger(__name__)


async def verify_twilio_signature(request: Request) -> None:
    """Reject webhook calls that are not signed by Twilio with our auth token."""
    settings: Settings = request.app.state.settings
    if not settings.twilio_validate_signature:
        return

    signature = request.headers.get("X-Twilio-Signature", "")
    # Behind ngrok the local URL differs from the one Twilio signed, so rebuild the public one.
    url = f"{settings.public_https_base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    form = await request.form()
    params = {key: str(value) for key, value in form.multi_items()}

    if not signature or not RequestValidator(settings.twilio_auth_token).validate(
        url, params, signature
    ):
        logger.warning("Rejected unsigned or invalid Twilio webhook for %s", request.url.path)
        raise HTTPException(status_code=403, detail="Forbidden")
