import os

os.environ.update(
    {
        "NGROK_DOMAIN": "example.ngrok.app",
        "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
        "TWILIO_AUTH_TOKEN": "test-token",
        "TWILIO_CALLER_NUMBER": "+15550000001",
        "TWILIO_AGENT_NUMBER": "+15550000002",
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
        "DEEPGRAM_API_KEY": "dg",
        "OPENAI_API_KEY": "oa",
        "ELEVEN_API_KEY": "el",
        "TWILIO_VALIDATE_SIGNATURE": "true",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402

from src.api_server import create_app  # noqa: E402

client = TestClient(create_app())


def test_unsigned_webhook_is_rejected() -> None:
    resp = client.post("/outbound-call", data={"Caller": "+15551234567"})
    assert resp.status_code == 403


def test_signed_webhook_is_accepted() -> None:
    params = {"Caller": "+15551234567"}
    signature = RequestValidator("test-token").compute_signature(
        "https://example.ngrok.app/outbound-call", params
    )
    resp = client.post("/outbound-call", data=params, headers={"X-Twilio-Signature": signature})
    assert resp.status_code == 200
    assert "<Say>" in resp.text


def test_health_needs_no_signature() -> None:
    assert client.get("/live").status_code == 200
