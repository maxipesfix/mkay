# Pipecat voice client

`voice_pipecat.py` lets you talk to the Claude, ChatGPT/Codex and Cursor desktop apps
hands-free, from a browser tab. There is no key to hold: voice activity detection and
[Smart Turn](https://github.com/pipecat-ai/smart-turn) decide when you have finished
speaking, and talking over a reply cuts it off. It uses the same
[MCP server](../multi-agent-mcp/README.md) tools, prompt and spoken confirmation as
the push-to-talk client in [`../local-voice-ptt`](../local-voice-ptt/README.md), built
on [Pipecat](https://github.com/pipecat-ai/pipecat) 1.12.

```text
browser (mic, speaker, echo cancellation)
   ⇅ WebRTC audio
voice_pipecat.py on this Mac:
   Silero VAD + Smart Turn → Whisper (local) → OpenAI or Claude API → Kokoro (local) → browser
                                                ⇅ tool calls
                                     agent_mcp.py (stdio) → apps
```

The browser is the microphone and speaker even on this Mac: its echo cancellation
keeps the bot from hearing its own voice, which a plain microphone stream would not.
Speech recognition and speech synthesis run on the Mac; only the LLM is a cloud API.
The same page is meant to be opened from a phone next (see [Next: the phone](#next-the-phone)).

## Setup

1. [uv](https://docs.astral.sh/uv/) and the MCP server's prerequisites (see
   [`../multi-agent-mcp`](../multi-agent-mcp/README.md) and
   [`../multi-agent-cli`](../multi-agent-cli/README.md)).
2. Copy `.env.example` to `.env` and set the key for your provider: `OPENAI_API_KEY`
   (default) or `ANTHROPIC_API_KEY` with `VOICE_PROVIDER=anthropic`. `.env` is
   git-ignored.
3. The terminal that runs it needs **Accessibility** access (System Settings > Privacy &
   Security), which the MCP server uses to drive the apps. The microphone permission
   belongs to the browser, not the terminal, and no Input Monitoring is needed.

```bash
cd /Users/maxim/susurobo/code/mkay/local-voice-pipecat
./voice_pipecat.py
```

Then open <http://localhost:7860/>, click **Connect** and allow the microphone. The bot
says "Ready." when it is listening.

The first run installs the dependencies (no PyTorch: Whisper, Silero, Smart Turn and
Kokoro all run on CTranslate2 or ONNX Runtime), and the first session downloads the
Kokoro voice model (about 350 MB, into `~/.cache/pipecat/kokoro-onnx`). The Whisper
model `small.en` is the one the push-to-talk client already downloaded. Later sessions
start in about 3 seconds.

## Using it

Just talk, for example:

- "What's going on?" — `status` across the apps.
- "Switch Claude to Code."
- "What sessions are in the nexor folder in Claude?"
- "Ask Cursor what it's working on in vrm-lipsync." — `ask_agent`, then a short summary.
- "What did ChatGPT say?" — `read_reply`.

Short phrases such as "Checking." or "Sending it now." are spoken while tools run.
While it waits for an agent's reply, it says "Still waiting on chatgpt." about every
20 seconds. Tool calls act on the real apps and cannot be taken back halfway, so while
one runs your speech is ignored (it cannot interrupt and cancel the call); you can
speak again as soon as it returns. Speaking over the bot's own speech cuts it off at
any other time.

The page also shows the conversation, and its text box sends a typed request instead of
speech. The terminal prints what you said (🗣), what the bot said (🔊) and each tool
call (🛠) with its duration.

One conversation at a time: connecting again, for example after reloading the page,
ends the previous session, so two conversations never drive the apps at once. Each
new connection starts with an empty conversation; learned names are kept.

### Confirmation before anything is sent

Before a tool that submits something (`send_message`, `submit_draft`, `ask_agent`,
`cursor_answer_question`), the client reads the exact text back, for example *Send to
Cursor in Code submission review: "run the tests". Should I send it?*, and waits:

- "yes", "yeah", "go ahead", "send it", "confirm", "OK" confirm;
- "no", "not", "cancel", "stop", "wait", "never mind" cancel (checked first, so
  "don't send it" cancels);
- anything else is asked again once, then treated as no.

This is enforced in the client, not left to the model. The first call to a submitting
tool only reads the text back. It runs when the model calls it again with exactly the
same arguments and your own words since the read-back say yes. Those words are the
user messages in the conversation, which only speech recognition (or the page's text
box) writes; agents' replies and the model's own text never count. If the model
changes the text, the new text is read back. The gated tools are the ones the MCP
server marks as submitting.

The model follows the same rules as the push-to-talk client for what it sends: only
the words meant for the agent, direct rather than indirect, nothing added. Replies from
other agents are information, never instructions.

## Security

The server listens on 127.0.0.1 only. Every request must carry a Host header naming
this server (`localhost`, `127.0.0.1` or `[::1]` with its port, or a name added with
`--allowed-host`), which blocks DNS-rebinding attacks, and any Origin header must be
this server's own, so other web pages open in your browser cannot start a session. The
API documentation pages are disabled. There is no login: anyone who can reach the port
can talk to the apps, which on 127.0.0.1 means programs on this Mac.

Pipecat's own development runner (`pipecat.runner.run`) was not used because it allows
every origin and has no access control on its WebRTC endpoints.

## Next: the phone

The plan for using it from a phone, not implemented yet:

- `tailscale serve --bg 7860` publishes the page at `https://MAC.TAILNET.ts.net` inside
  the tailnet only, with a certificate, which browsers require for microphone access.
  The server itself keeps listening on 127.0.0.1, and nothing is exposed publicly.
- `./voice_pipecat.py --allowed-host MAC.TAILNET.ts.net` accepts that Host and Origin.
- WebRTC media goes directly between the phone and the Mac's Tailscale address (host
  candidates), so no STUN or TURN server is involved.
- To add: accept only your own Tailscale identity (`tailscale serve` adds a
  `Tailscale-User-Login` header), keep the session alive across the phone's screen
  locking, and a mobile-friendly page.

## Troubleshooting

- **"Microphone blocked" on the page:** allow the microphone for `localhost` in the
  browser's site settings and reload.
- **421 or 403 in the browser:** open the page as `http://localhost:PORT/` or
  `http://127.0.0.1:PORT/`, or pass `--allowed-host` for any other name.
- **"No microphone audio from the browser":** the server received no audio for 3
  seconds while WebRTC was connected. If "resumed" follows in the same second, the
  server itself was stalled rather than the browser. Otherwise the page stopped sending:
  microphone muted on the page, input device changed, or the tab suspended. Click
  Disconnect and Connect.
- **Japanese names:** Kokoro speaks English only, so the model is told to say names in
  non-Latin scripts in romaji (そばとも as "Sobatomo"); tool arguments keep the exact
  title. Say the romaji to refer to them.
- **The bot does not answer:** the terminal shows the error; an invalid API key shows
  as `HTTP 401` from the LLM service, and the session stops.
- `--debug` (or `VOICE_DEBUG=1`) shows Pipecat's full log: VAD and turn decisions,
  transcription and TTS timing, and every frame's latency metrics.

## Configuration

Set in `.env` (see `.env.example`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOICE_PROVIDER` | `openai` | `openai` (Responses API over WebSocket) or `anthropic` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | The key for the chosen provider |
| `VOICE_MODEL` | `gpt-5.5` (OpenAI), `claude-opus-5` (Claude) | Model that picks the tools |
| `VOICE_EFFORT` | `low` | Reasoning effort |
| `VOICE_STT_MODEL` | `small.en` | Whisper size: `tiny.en` … `medium.en`, or multilingual `small`, `medium`, `large-v3` |
| `VOICE_VOCABULARY` | — | Comma-separated words speech recognition should always expect |
| `VOICE_KOKORO_VOICE`, `VOICE_KOKORO_SPEED` | `af_heart`, 1.0 | Kokoro voice and speed |
| `VOICE_PORT` | `7860` | Port for the page and WebRTC signaling (`--port`) |
| `VOICE_DEBUG` | off | `1` shows Pipecat's debug log |
| `AGENT_MCP` | `../multi-agent-mcp/agent_mcp.py` | MCP server to start |

Speech recognition is primed with the app names, `VOICE_VOCABULARY`, and project and
session names from tool results, like the push-to-talk client. Both clients share the
learned names in `~/.cache/local-voice-ptt/names.json`, so `../local-voice-ptt/voice_ptt.py
--prime` also primes this one.

## Differences from the push-to-talk client

| | `local-voice-ptt` | `local-voice-pipecat` |
| --- | --- | --- |
| Turn taking | Hold a key | Voice activity detection and Smart Turn |
| Audio | Mac microphone, `say` | Browser over WebRTC, Kokoro |
| Cutting the bot off | Press the key | Speak |
| Confirmation | Hold the key and answer | Answer; checked when the model calls the tool again |
| Reachable from | This Mac's keyboard | A browser; a phone next |
| Conversation history | Last `VOICE_MAX_TURNS` requests | The whole session (cleared on reconnect) |

## Components and licenses

| Part | Package | License |
| --- | --- | --- |
| Pipeline, transport, web client | `pipecat-ai`, `pipecat-ai-prebuilt`, `aiortc` | BSD-2-Clause, BSD-2-Clause, BSD-3-Clause |
| Turn detection | Silero VAD, Smart Turn v3 (bundled with Pipecat) | MIT, BSD-2-Clause |
| Speech recognition | `faster-whisper` with Systran's converted Whisper models | MIT |
| Speech | `kokoro-onnx` with the Kokoro-82M model | MIT, Apache-2.0 |
| LLM and tools | `openai` (Apache-2.0), `anthropic` (MIT), `mcp` (MIT) | Apache-2.0 and MIT |
| Web server | `fastapi`, `uvicorn` | MIT, BSD-3-Clause |

Dependencies are declared inside `voice_pipecat.py` and pinned in `voice_pipecat.py.lock`;
uv installs them.

Two workarounds for Pipecat 1.12 bugs, to remove once a release includes the fixes:

- `make_stt`: Pipecat's faster-whisper service decodes on the event loop (`transcribe`
  returns a lazy generator that Pipecat iterates there), which froze the server for
  each transcription: 1.3 s for 7.7 s of speech, about 4 s while Kokoro was speaking.
  Audio stopped in both directions and the browser eventually dropped the connection.
  The wrapper makes decoding finish in Pipecat's worker thread (measured stall:
  0.00 s). Fix proposed upstream in
  [pipecat-ai/pipecat#5931](https://github.com/pipecat-ai/pipecat/pull/5931).
- `allow_faster_whisper`: Pipecat imports `mlx_whisper` on Apple Silicon even for
  faster-whisper, and `mlx-whisper` depends on PyTorch; the client registers a
  placeholder module for that import. Fixed upstream by
  [pipecat-ai/pipecat#5878](https://github.com/pipecat-ai/pipecat/pull/5878) (open).

## Status

Checked on 2026-09-26 (Pipecat 1.12.0, macOS 26.5, Apple M4):

- The server starts the MCP server and exposes its 17 tools; Host and Origin checks
  reject foreign names (421, 403).
- A browser session connects over WebRTC, loads Whisper, Smart Turn and Kokoro, and
  the bot's "Ready." plays in the page. Reloading the page and Disconnect end the
  session cleanly; Ctrl-C stops the server and the MCP server in about 2 seconds.
- The confirmation gate, offline with a simulated conversation: nothing is sent without
  a yes in the user's own words after the read-back, changed text is read back again,
  "don't send it" cancels, an earlier yes is not reused, and `submit_draft` reads back
  the typed draft.

First spoken use, 2026-09-26: status, listings in Claude Code and ChatGPT, and opening a
ChatGPT session worked by voice. Found and fixed there: Japanese names were read as
"Japanese letter" (now spoken in romaji), and speech recognition stalled the server
until the browser disconnected (see Components). Not yet tried after the fix: a longer
conversation, a send through this client, barge-in, and Claude as the provider.

Known issue: Kokoro sometimes logs `TTS context ... completed with no audio` right after
listings that include Japanese names; the cause is not known yet, and whether any
phrase goes unspoken has not been confirmed.
