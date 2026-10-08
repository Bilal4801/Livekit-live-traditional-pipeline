# Live Voice Translation — Twilio + LiveKit + Deepgram → OpenAI → ElevenLabs

Bidirectional live voice translation for **Spanish-speaking callers** and **English-speaking
contact center agents**. The caller hears Spanish only (IVR, hold message, translated agent voice);
the agent hears English only.

Same call-control architecture as `E:\Livekit-live-translation` (Twilio Studio, Flex / TaskRouter,
LiveKit Cloud Twilio Connector), but the two GPT-Live speech-to-speech sessions are replaced by a
**traditional streaming pipeline** in each direction:

```
speaker audio → Deepgram STT (nova-3, interim results)
             → eager word committer   (releases each new word immediately)
             → OpenAI LLM             (translates only the new words, continuing the sentence)
             → ElevenLabs TTS         (flash v2.5, one flushed context per fragment)
             → translated track for the other party
```

| Direction | Pipeline |
|-----------|----------|
| Caller speaks | Spanish → English → agent hears (`to_agent` track) |
| Agent speaks | English → Spanish (formal *usted*, neutral Latin American) → caller hears (`to_caller` track) |

The translator prompts (`src/prompts.py`) include Spanish ⇄ English rules for word-by-word
interpreting: adding the subject Spanish drops ("quiero" → "I want"), noun/adjective order,
idioms by meaning, and exact numbers.

## Word-by-word (simultaneous) translation

The system does **not** wait for a phrase to finish:

1. Deepgram streams interim transcripts while the person talks. The committer
   (`src/agent/eager_committer.py`) releases every word the moment it first appears
   (`EAGER_HOLDBACK_WORDS=0`).
2. Each fragment goes to the LLM with the utterance so far and the translation already spoken,
   so it outputs only the *continuation*. While one request is running, new words queue up
   and are sent together in the next request — latency stays at about one LLM round trip.
3. Each translated fragment opens its own ElevenLabs context and is flushed immediately, so even
   a single translated word starts playing right away. Fragments play back strictly in order.

Trade-off: translating before the sentence is complete can produce less natural word order, and
Deepgram sometimes revises the latest interim word. Set `EAGER_HOLDBACK_WORDS=1` to wait for one
following word before translating (more accurate, slightly slower).

## Prerequisites

1. Twilio Flex account (or plain Twilio Voice with `SKIP_FLEX=true`)
2. LiveKit **Cloud** project (Twilio Connector is Cloud-only)
3. Deepgram API key, OpenAI API key, ElevenLabs API key
4. Second Twilio phone number (caller-facing, not the Flex number)
5. Python 3.10+ and ngrok

## Setup

```bash
cd E:\Livekit-live-traditional-pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then fill in credentials
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio credentials |
| `TWILIO_CALLER_NUMBER` / `TWILIO_AGENT_NUMBER` | Caller-facing number / agent leg number |
| `TWILIO_FLEX_WORKFLOW_SID` | Flex workflow SID (Flex mode only) |
| `TWILIO_VALIDATE_SIGNATURE` | Reject webhooks without a valid `X-Twilio-Signature` (default `true`) |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit Cloud project |
| `DEEPGRAM_API_KEY` / `DEEPGRAM_MODEL` | Deepgram STT (default `nova-3`) |
| `DEEPGRAM_ENDPOINTING_MS` | Silence that ends an utterance (default `25`) |
| `OPENAI_API_KEY` / `OPENAI_LLM_MODEL` | Translation LLM (default `gpt-5.4-mini`, reasoning off) |
| `ELEVEN_API_KEY` / `ELEVENLABS_MODEL` | ElevenLabs TTS (default `eleven_flash_v2_5`) |
| `ELEVENLABS_VOICE_ID` | English voice the agent hears |
| `ELEVENLABS_CALLER_VOICE_ID` | Spanish voice the caller hears (empty = same voice) |
| `CALLER_LANGUAGE` | `spanish` or `english` (default `spanish`) |
| `EAGER_HOLDBACK_WORDS` | `0` = translate every word immediately; `1+` = wait for N following words |
| `TRANSLATION_HISTORY_TURNS` | Previous utterances given to the LLM for context |
| `NGROK_DOMAIN` | ngrok hostname only (no `https://`) |
| `SKIP_FLEX` | `true` = plain phone agent, no TaskRouter |
| `LOG_TRANSCRIPTS` | Log speech and translations (personal data — keep off in production) |

## Run (three terminals)

```bash
python -m src.api_server            # 1) Twilio webhook API
python -m src.agent_worker dev      # 2) LiveKit translation worker
ngrok http 5050                     # 3) Public tunnel for Twilio → FastAPI
```

Copy the ngrok hostname into `NGROK_DOMAIN` and into the Studio Flow redirect URL.

## Twilio setup

1. Import [`inbound_language_studio_flow.json`](inbound_language_studio_flow.json) into Twilio
   Studio, replace `[your-ngrok-domain]`, publish. The IVR speaks Spanish ("…oprima 1") and
   redirects with `?lang=spanish`. A missing or unknown `lang` falls back to `CALLER_LANGUAGE`.
2. Point `TWILIO_CALLER_NUMBER` → Studio Flow.
3. Flex mode only: point `TWILIO_AGENT_NUMBER` voice webhook to `https://[ngrok]/outbound-call`,
   and the TaskRouter event callback to `https://[ngrok]/reservation-accepted`
   (event **Reservation Accepted**).

With `lang=auto`, the caller lane uses Deepgram `multi` and the agent → caller lane starts once the
caller's language is detected (Deepgram's detected language, falling back to an OpenAI classifier).

## Tests

```bash
python -m pytest -q
```

## Project layout

```
src/
  api_server.py              # FastAPI Twilio webhooks
  agent_worker.py            # LiveKit Agents worker
  security.py                # Twilio webhook signature validation
  prompts.py                 # Simultaneous-interpreter instructions
  routes/                    # incoming-call, agent-answered, Flex hooks
  services/                  # CallSession store + ConnectTwilioCall bridge
  agent/
    pipeline.py              # One streaming STT -> LLM -> TTS lane
    eager_committer.py       # Interim transcript -> new words
    audio_interceptor.py     # Two lanes + track isolation / permissions
    translation_agent.py     # Job lifecycle / start gating
    languages.py             # Language names and Deepgram / ElevenLabs codes
```
