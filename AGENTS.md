# Live Voice Translation (Twilio + LiveKit + Deepgram/OpenAI/ElevenLabs)

Middleware + LiveKit agent that provides bidirectional, word-by-word live voice translation
between a caller and a human agent using a streaming STT -> LLM -> TTS pipeline.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env

python -m src.api_server          # Terminal 1 — Twilio webhooks
python -m src.agent_worker dev    # Terminal 2 — LiveKit agent
ngrok http 5050                   # Terminal 3 — expose FastAPI to Twilio
python -m pytest -q               # tests
```

## Environment

Copy `.env.example` to `.env`. Never commit `.env`.

Required: Twilio credentials + numbers, LiveKit Cloud `LIVEKIT_URL` / API key / secret,
`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `ELEVEN_API_KEY`, `NGROK_DOMAIN`.

## Structure

- `src/routes/incoming_call.py` — Studio webhook; ConnectTwilioCall INBOUND; dial agent
- `src/routes/agent_answered.py` — AMD gate; ConnectTwilioCall OUTBOUND
- `src/routes/outbound_call.py` — Flex enqueue TwiML
- `src/routes/reservation_accepted.py` — arms translation after Flex accept
- `src/security.py` — `X-Twilio-Signature` validation on every Twilio webhook
- `src/agent/pipeline.py` — streaming lane: Deepgram interim words -> OpenAI -> ElevenLabs
- `src/agent/audio_interceptor.py` — two lanes + track isolation
- `src/prompts.py` — simultaneous-interpreter instructions

## Agent boundaries

**Always**

- Confirm `.env` is complete and ngrok matches Studio + phone webhooks before testing
- Remind that `NGROK_DOMAIN` must update whenever ngrok restarts (signature checks use it)
- Flex: Agent Desktop Available before placing the test call

**Never**

- Start a test call before Twilio Console + LiveKit Cloud credentials are set
- Dial `TWILIO_AGENT_NUMBER` for the test — dial `TWILIO_CALLER_NUMBER`
- Hardcode secrets or phone numbers in source

## Verify

1. API logs listening; agent worker shows registered as `live-translator`
2. Call caller number → pick language → agent answers
3. Speech on either side is translated for the other party while they are still speaking
