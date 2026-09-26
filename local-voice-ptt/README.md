# Local push-to-talk voice client

`voice_ptt.py` lets you drive the Claude, ChatGPT/Codex and Cursor desktop apps by
voice on this Mac. Hold a key, say what you want, release. Your request is
transcribed on the Mac, an LLM (OpenAI by default, or Claude) decides which
[MCP server](../multi-agent-mcp/README.md) tools to call, and the answer is spoken with
macOS `say`.

This is the simple, turn-by-turn prototype: one request at a time, no continuous
listening. [`../local-voice-pipecat`](../local-voice-pipecat/README.md) is the
hands-free client, with turn detection and barge-in, used from a browser.

```text
hold key → microphone → Whisper (local) → OpenAI or Claude API → agent_mcp.py tools → apps
                                              ↓
                                   spoken reply via `say`
```

## Setup

1. [uv](https://docs.astral.sh/uv/) and the MCP server's prerequisites (see
   [`../multi-agent-mcp`](../multi-agent-mcp/README.md) and
   [`../multi-agent-cli`](../multi-agent-cli/README.md)).
2. Copy `.env.example` to `.env` and set the key for your provider: `OPENAI_API_KEY`
   (default) or `ANTHROPIC_API_KEY` with `VOICE_PROVIDER=anthropic`. `.env` is
   git-ignored.
3. Give the terminal that runs the client these macOS permissions (System Settings >
   Privacy & Security), then restart it:
   - **Microphone**, to record you (macOS asks on first use).
   - **Input Monitoring**, to read the push-to-talk key while other apps are in front.
   - **Accessibility**, which the MCP server it starts needs to drive the apps.

```bash
cd /Users/maxim/susurobo/code/mkay/local-voice-ptt
./voice_ptt.py            # voice
./voice_ptt.py --text     # type requests, print replies (no microphone or speech)
```

The first run installs the dependencies and downloads the speech model (about
500 MB for `small.en`), so it takes a minute; later starts take a few seconds.

## Using it

Hold **right Command** (configurable), speak, and release. It is the default because it exists on both US and Japanese (JIS)
keyboards, which have no right Option, and does nothing on its own. The key still
reaches the app in front: 英数 and かな switch the input source, and fn may open the
emoji or dictation panel, depending on your keyboard settings.

For example:

- "What's going on?" — `status` across the apps.
- "Switch Claude to Code."
- "What sessions are in the nexor folder in Claude?"
- "Ask Cursor what it's working on in vrm-lipsync." — `ask_agent`, then a short summary.
- "What did ChatGPT say?" — `read_reply`.
- "Cursor has a question. Pick B." — `cursor_answer_question`.

The microphone opens once at startup and keeps the last half second before you
press the key, so recording starts instantly and your first word isn't clipped.
Recordings stop after `VOICE_MAX_RECORD_SECONDS` (60 by default) even if the key still
reads as held.

Speech recognition is primed with names it would otherwise mishear: the app names,
the words in `VOICE_VOCABULARY`, and every project and session name that tool results
have returned. Learned names are kept in `~/.cache/local-voice-ptt/names.json` (outside
the repository, since they are private) and reloaded at startup. Without priming,
"nexor" was heard as "next hour" or "Nexer" and "mkay" as "MKE"; primed, both come out
right.

To learn every name up front, scan the apps once:

```bash
./voice_ptt.py --prime
```

It lists projects and sessions in both views of each app, saves the names, switches
each app back to the view it started in, and exits; no API key is used. The apps come
to the front and switch views while it runs, Claude's Chat view opens its Projects
page, and collapsed sidebar folders are expanded. It takes under a minute; a scan of
Cursor and ChatGPT took 20 seconds. Rerun it when you add projects.
`VOICE_PRIME_APPS=cursor,chatgpt` limits the scan (for example to leave Claude alone
while you work in it), and `VOICE_PRIME_ON_START=1` scans at every startup. Interface
rows such as "New chat in …", "Show more" and untitled-chat IDs are not learned.

For names you use before the client has listed them, add them to `.env`:

```bash
VOICE_VOCABULARY=nexor, mkay, vrm-lipsync, pipecat, signoz
```

Press the key while it's talking to cut it off and start your next request. Short
phrases such as "Checking." or "Sending it now." are spoken while tools run, since
each takes a few seconds. While it waits for an agent's reply, which can take a
minute or more (ChatGPT searching the web took 70 seconds), it says "Still waiting on
chatgpt." about every 20 seconds.

### Confirmation before anything is sent

Before a tool that submits something (`send_message`, `submit_draft`, `ask_agent`,
`cursor_answer_question`), the client reads it back, for example *Send to Cursor in
Code submission review: "run the tests". Should I send it?*, and waits for your
answer. **Hold the key again** to answer; the prompt says so:

- "yes", "yeah", "go ahead", "send it", "confirm", "OK" confirm;
- "no", "not", "cancel", "stop", "wait", "never mind" cancel (checked first, so
  "don't send it" cancels);
- anything else is asked again once, then treated as no.

The gated tools are the ones the MCP server marks as submitting, so the list follows
the server. When you say "send it" after the client typed a draft, the confirmation
reads that draft back rather than just "the draft".

### What gets sent

Only the part of your request meant for the agent becomes the message. The model
drops what is addressed to it (which app, project or session; "open", "type", "send",
"ask", "a follow-up"), turns indirect requests into direct ones, and adds nothing you
did not say:

| You say | Sent |
| --- | --- |
| "In ChatGPT, send a follow-up asking if MA crossings also work for crypto." | "Do MA crossings also work for crypto?" |
| "Tell Cursor to please run the tests and let me know if anything fails." | "Please run the tests and let me know if anything fails." |

If it can't tell which words are for the agent, it asks. The model also treats
replies from other agents as information, never as instructions to act on, and waits
again when a reply looks like the agent has only started ("I'll check that now").

Quit with Ctrl-C (or Ctrl-D in `--text` mode). It prints "Stopping…", shuts the MCP
server down and exits; if that stalls, it force-quits after 3 seconds, and a second
Ctrl-C force-quits at once.

## Troubleshooting

If holding the key does nothing (no "🎙 listening..." line):

```bash
./voice_ptt.py --debug-keys
```

It prints whether this terminal has Input Monitoring and Accessibility access, then
for 20 seconds lists every key and modifier change it detects. Hold your push-to-talk
key: the line should end with `push-to-talk down: yes`, and right Command shows the
flag `right_command` (bit `0x10`). Some keyboards and key remappers report right
Command with left Command's key code (55); the client also checks the flag bit, so
right Command still works there. If nothing
changes while you press keys, enable the terminal under System Settings > Privacy &
Security > Input Monitoring and restart it. If a different key code appears, use a
matching `VOICE_PTT_KEY`.

`./voice_ptt.py --debug` (or `VOICE_DEBUG=1`) adds details during normal use: the
input device, key down and up, the key's raw state every 3 seconds while recording,
recording length and loudness (a level near 0 means silence or no microphone
access), the names used to prime recognition, transcription time, and how long each
model response and tool call took.

## Configuration

Set in `.env` (see `.env.example` for all options):

| Variable | Default | Meaning |
| --- | --- | --- |
| `VOICE_PROVIDER` | `openai` | `openai` or `anthropic` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | The key for the chosen provider |
| `VOICE_MODEL` | `gpt-5.5` (OpenAI), `claude-opus-5` (Claude) | Model that picks the tools; `gpt-5.4-mini` answers faster |
| `VOICE_EFFORT` | `low` | Reasoning effort; `low` answers fastest, raise to `medium` or `high` if tool choices go wrong |
| `VOICE_PTT_KEY` | `right_command` | `right_command`, `right_option`, `left_option`, `right_control`, `eisu` (英数), `kana` (かな), `fn`, `f13`–`f15` |
| `VOICE_STT_MODEL` | `small.en` | Whisper size: `tiny.en` … `medium.en`, or multilingual `small`, `medium`, `large-v3` |
| `VOICE_SAY_VOICE`, `VOICE_SAY_RATE` | System default | macOS voice (`say -v '?'` lists them) and words per minute |
| `VOICE_MAX_TURNS` | `12` | Earlier requests kept as context |
| `VOICE_MAX_RECORD_SECONDS` | `60` | Longest recording |
| `VOICE_VOCABULARY` | — | Comma-separated words speech recognition should always expect |
| `VOICE_PRIME_APPS` | all | Apps `--prime` scans, comma-separated |
| `VOICE_PRIME_ON_START` | off | `1` runs the `--prime` scan at every startup |
| `AGENT_MCP` | `../multi-agent-mcp/agent_mcp.py` | MCP server to start |

## Providers

Both providers get the same system prompt and the MCP server's tool schemas, and
both keep the last `VOICE_MAX_TURNS` requests as context.

- **OpenAI** uses the Responses API. Its Chat Completions endpoint rejects function
  tools combined with reasoning effort for models such as `gpt-5.5`. Requests are sent
  with `store=False`, so OpenAI keeps no conversation state between requests; the
  client sends the history each time, with the model's reasoning passed back in
  encrypted form.
- **Claude** uses the Messages API with the settings below.

Claude requests use server-side refusal fallbacks (`fallbacks: "default"`): if the
model declines a request on policy grounds, the API re-runs it on Anthropic's
recommended fallback model instead of returning a refusal. The system prompt and tool
list are identical on every request and are cached, which cuts cost and latency after
the first turn.

## Components and licenses

| Part | Package | License |
| --- | --- | --- |
| Speech recognition | `faster-whisper` with Systran's converted Whisper models | MIT (package and models) |
| Audio capture | `sounddevice`, `numpy` | MIT, BSD |
| LLM and tools | `openai` (Apache-2.0), `anthropic` (MIT), `mcp` (MIT) | Apache-2.0 and MIT |
| Push-to-talk key | macOS `CGEventSourceKeyState` through `ctypes` | Part of macOS |
| Speech | macOS `say` | Part of macOS |

Dependencies are declared inside `voice_ptt.py` and installed by uv; nothing is
vendored into this repository.

## Status

Used live by voice on 2026-09-25 with OpenAI `gpt-5.5`, on a MacBook Pro microphone
and a keyboard that reports right Command with left Command's key code:

- Listing projects and sessions in ChatGPT, Codex, Claude Code and Cursor, including
  ChatGPT projects behind "Show more" and a collapsed project whose chats load late.
- Sending to a new ChatGPT chat, and to existing ChatGPT and Claude sessions, with
  spoken confirmation, then reading and summarizing the reply (one ChatGPT answer took
  70 seconds with web search; "Still waiting" kept the silence short).
- Typing a draft, then submitting it after a read-back.
- Ctrl-C quitting cleanly.

Checked in `--text` mode: message composition (only the words meant for the agent are
sent), declining at the confirmation sends nothing, and API errors are spoken (the
first Claude run stopped at "credit balance is too low"). Claude as the provider has
not been used beyond that error, and no Cursor message has been sent by voice yet.

Navigation tools such as `set_mode` and `open_session` run without confirmation, so
the model may switch an app's view or open a session when a request needs it.
