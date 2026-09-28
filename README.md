# mkay

Drive AI agent desktop apps on macOS (Claude, ChatGPT/Codex and Cursor) from a
terminal, from LLM clients, and by voice.

| Directory | What it is |
| --- | --- |
| [`multi-agent-cli`](multi-agent-cli/README.md) | `agent_ctl.py`: the CLI and per-app backends that drive each app's interface through macOS accessibility. Standard-library Python only. |
| [`multi-agent-mcp`](multi-agent-mcp/README.md) | `agent_mcp.py`: an MCP server exposing the CLI as tools to local (stdio) and remote (HTTP with a bearer token) LLM clients. Runs with uv. |
| [`local-voice-ptt`](local-voice-ptt/README.md) | `voice_ptt.py`: a push-to-talk voice client. Local Whisper, an LLM (OpenAI by default, or Claude) choosing MCP tools, spoken replies via `say`, spoken confirmation before anything is sent. |
| [`macos-app`](macos-app/README.md) | `m’kay.app`: a menu-bar app, distributed as a disk image, that links this Mac to a voice server. A Swift shell running the connector with a bundled Python; setup of Accessibility, Automation and sign-in, no terminal needed. |
| [`local-voice-pipecat`](local-voice-pipecat/README.md) | `voice_pipecat.py`: a hands-free voice client on Pipecat, used from a browser over WebRTC. Turn detection and barge-in, cloud or local speech and a choice of voices and models on its page, the same tools and spoken confirmation. |

Each layer uses only the one below it: the MCP server runs the CLI's commands, and
voice clients call the MCP server's tools.

## Talk to your Mac, running everything yourself

The local voice stack runs on your Mac: a Pipecat server that serves a web page, listens
through the browser, and drives the apps with the MCP server's tools. Only the LLM (and
cloud speech, if you choose it) is called over the internet.

**You need:** macOS with the Claude, ChatGPT or Cursor desktop app,
[uv](https://docs.astral.sh/uv/) (`brew install uv`), and an OpenAI API key (or an
Anthropic key). Deepgram, Cartesia and Fish Audio keys are optional: without them,
speech runs on your Mac with Whisper and Kokoro.

1. Get the code and set your key:

   ```bash
   git clone https://github.com/maxipesfix/mkay.git
   ```

   ```bash
   cd mkay/local-voice-pipecat && cp .env.example .env
   ```

   Then set `OPENAI_API_KEY=` in `local-voice-pipecat/.env` (and any optional keys).

2. Start the voice server (the first run installs its dependencies, a few minutes):

   ```bash
   ./voice_pipecat.py
   ```

3. Give the terminal app you started it from **Accessibility** access (System Settings →
   Privacy & Security → Accessibility), then restart the server. The first time it
   presses keys in an app, macOS also asks to let the terminal control **System Events**;
   allow it.

4. Open <http://localhost:7860/> in Chrome or Safari, choose **Listening**, **Voice** and
   **Model** under the circle, click **Start talking**, and allow the microphone. Try
   "What's going on?" or "What projects do I have in ChatGPT?". Nothing is sent to an
   agent until you say yes to the read-back.

The first session with local speech downloads the Whisper and Kokoro models (about
0.8 GB). See [`local-voice-pipecat`](local-voice-pipecat/README.md) for settings,
troubleshooting and running it as a library; to talk from your phone instead, the
[m’kay app](macos-app/README.md) links your Mac to the hosted service at
[mkay.ai](https://mkay.ai).

Licensed under the BSD 2-Clause License; see [LICENSE](LICENSE).
