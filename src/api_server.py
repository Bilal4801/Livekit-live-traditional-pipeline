from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

from src.config import get_settings
from src.routes import agent_answered, health, incoming_call, outbound_call, reservation_accepted
from src.services.twilio_livekit import TwilioLiveKitBridge

load_dotenv()


def create_app() -> FastAPI:
    # Clear cached settings so a freshly written .env is picked up
    get_settings.cache_clear()
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logging.getLogger(__name__).info(
            "Translation API listening — agent=%s skip_flex=%s signature_check=%s",
            settings.agent_name,
            settings.skip_flex,
            settings.twilio_validate_signature,
        )
        yield

    is_dev = settings.app_env == "development"
    app = FastAPI(
        title="LiveKit Live Translation API",
        description="Twilio webhooks + LiveKit Twilio Connector for STT->LLM->TTS translation",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_dev else None,
    )
    app.state.settings = settings
    app.state.bridge = TwilioLiveKitBridge(settings)

    app.include_router(health.router)
    app.include_router(incoming_call.router)
    app.include_router(agent_answered.router)
    app.include_router(outbound_call.router)
    app.include_router(reservation_accepted.router)
    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "src.api_server:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
    )


if __name__ == "__main__":
    main()
