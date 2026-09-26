# mkay

Drive AI agent desktop apps on macOS (Claude, ChatGPT/Codex and Cursor) from a
terminal, from LLM clients, and by voice.

| Directory | What it is |
| --- | --- |
| [`multi-agent-cli`](multi-agent-cli/README.md) | `agent_ctl.py`: the CLI and per-app backends that drive each app's interface through macOS accessibility. Standard-library Python only. |
| [`multi-agent-mcp`](multi-agent-mcp/README.md) | `agent_mcp.py`: an MCP server exposing the CLI as tools to local (stdio) and remote (HTTP with a bearer token) LLM clients. Runs with uv. |
| [`local-voice-ptt`](local-voice-ptt/README.md) | `voice_ptt.py`: a push-to-talk voice client. Local Whisper, an LLM (OpenAI by default, or Claude) choosing MCP tools, spoken replies via `say`, spoken confirmation before anything is sent. |
| [`local-voice-pipecat`](local-voice-pipecat/README.md) | `voice_pipecat.py`: a hands-free voice client on Pipecat, used from a browser over WebRTC. Turn detection and barge-in, local Whisper and Kokoro, the same tools and spoken confirmation. A phone is next. |

Each layer uses only the one below it: the MCP server runs the CLI's commands, and
voice clients will call the MCP server's tools.

Licensed under the BSD 2-Clause License; see [LICENSE](LICENSE).
