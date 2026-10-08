from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv
from livekit.agents import AgentServer, JobContext, JobProcess, cli

from src.agent.translation_agent import TranslationJob
from src.config import get_settings

load_dotenv()

logger = logging.getLogger("live-translator")

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Warm worker process; STT/LLM/TTS clients are created per job."""
    get_settings.cache_clear()
    proc.userdata["settings"] = get_settings()


server.setup_fnc = prewarm


# agent_name enables explicit dispatch from ConnectTwilioCall (must match AGENT_NAME / .env)
@server.rtc_session(agent_name="live-translator")
async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    if settings.agent_name != "live-translator":
        logger.warning(
            "AGENT_NAME=%s but worker registers as live-translator — "
            "update the decorator or set AGENT_NAME=live-translator",
            settings.agent_name,
        )
    logger.info("Job started room=%s job_id=%s", ctx.room.name, ctx.job.id if ctx.job else None)
    job = TranslationJob(ctx, settings)
    await job.run()


def main() -> None:
    cli.run_app(server)


if __name__ == "__main__":
    main()
