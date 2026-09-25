# Multi-agent MCP server

`agent_mcp.py` is an MCP server that gives LLM clients the full command set of
[`agent_ctl.py`](../multi-agent-cli/README.md) as tools, to control the Claude,
ChatGPT/Codex and Cursor desktop apps on this Mac. Each tool runs `agent_ctl.py` as a
child process, so it behaves exactly like the CLI, with the same checks and refusals. Tool calls run one at a time, because each app has
a single interface to drive. The server uses the official `mcp` Python SDK (2.x); its
dependencies are declared inside the script and pinned in `agent_mcp.py.lock`, and
`uv run --script` installs them on first use. The CLI stays dependency-free.

## Setup

- [uv](https://docs.astral.sh/uv/). The first run installs the pinned dependencies.
- The CLI in `../multi-agent-cli`, set up as its README describes (Accessibility
  permission, apps installed and signed in). The server runs
  `../multi-agent-cli/agent_ctl.py` by default; set `AGENT_CTL` to use another copy.
  It reads the app list from `agent_ctl.py apps --json` at startup and refuses to
  start if the CLI is missing.

```text
multi-agent-mcp/
  README.md
  agent_mcp.py                  # MCP server (stdio and HTTP)
  agent_mcp.py.lock             # Pinned dependencies (uv)
  test/
    test_mcp_server.py          # Live smoke test (read-only tools, refusals, HTTP auth)
```

## Tools

| Tool | CLI command | Kind |
| --- | --- | --- |
| `list_apps` | — | Read: apps, their view names, and app-only tools |
| `status(apps?)` | `status` for each app | Read: view, busy, pending question, Claude's running and unread sessions |
| `get_mode(app)` | `mode` | Read |
| `set_mode(app, mode)` | `mode VIEW`, then `mode` to confirm | Navigate |
| `list_projects(app)` | `projects` | Read |
| `list_sessions(app, project?, recents?)` | `sessions [--project NAME \| --recents]` | Read |
| `open_session(app, title)` | `session TITLE` | Navigate |
| `read_reply(app)` | `read` | Read; returns `reply` and, for Cursor, `pending_question` |
| `wait_for_reply(app, timeout_seconds?)` | `status` and `read`, polled | Read: waits until the agent finishes |
| `ask_agent(app, message, session?, timeout_seconds?)` | `session`, `send`, then waits | Submit: send and wait in one call |
| `type_text(app, text)` | `type TEXT` | Draft |
| `send_message(app, text)` | `send TEXT` | Submit |
| `submit_draft(app)` | `enter` | Submit |
| `cursor_open_project(name)` | `--app cursor project NAME` | Navigate |
| `cursor_answer_question(letter, text?)` | `--app cursor answer LETTER [TEXT]` | Submit; returns `done` or `next_question` with the question |
| `diagnose(app, kind)` | `debug-mode` / `debug-sidebar` | Read |

`app` is `claude`, `chatgpt` or `cursor`. Results are structured: lists come back as
arrays, and `read_reply` separates Cursor's pending question into a prompt and
lettered options. A failed command returns an error result carrying the CLI's own
message and exit status, which the calling model can read.

`wait_for_reply` and `ask_agent` poll every couple of seconds and return:

| `status` | Meaning |
| --- | --- |
| `done` | The agent is idle and its reply text was the same on two consecutive checks; `reply` holds it |
| `question` | A Cursor agent stopped on a multiple-choice question; `pending_question` holds it |
| `timeout` | `timeout_seconds` passed (default 300, at most 900); `reply` holds whatever is visible |

If the agent is idle and no reply can be read twice in a row, for example because no
conversation is open, the call fails at once with `Nothing to wait for:` and the
CLI's reason instead of waiting out the timeout.

`ask_agent` records the reply before sending and only accepts a different one, so an
old reply is never returned as the answer. After a `timeout` the message has already
been sent: call `wait_for_reply` again instead of resending. Long waits send MCP
progress notifications, which also keep HTTP clients from timing out.

The tools carry MCP hints so clients can decide what to confirm: listing and reading
tools are marked read-only, and `send_message`, `submit_draft` and
`cursor_answer_question` are marked as submitting, because a sent message or answer
cannot be taken back; `ask_agent` is marked the same way. The server's instructions
tell the calling model to treat agents' replies as information rather than
instructions, and to confirm the exact text with you before submitting unless you
dictated it. Tools still act on the real apps and bring them to the front,
even the read-only ones, so leave the apps alone while a client is using them.

## Local clients (stdio)

The MCP client starts the server itself. Use absolute paths, because desktop apps do
not see your shell's `PATH`:

Claude Code:

```bash
claude mcp add agent-ctl -- /opt/homebrew/bin/uv run --script /Users/maxim/susurobo/code/mkay/multi-agent-mcp/agent_mcp.py
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`) or
Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agent-ctl": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--script", "/Users/maxim/susurobo/code/mkay/multi-agent-mcp/agent_mcp.py"]
    }
  }
}
```

The server inherits macOS permissions from the app that starts it, so that app needs
Accessibility access too: for example Claude, Cursor or your terminal. When the client
is itself Claude or Cursor, tools that target the same app drive the window the client
runs in; `set_mode` on it switches that window's view.

## Remote clients (HTTP)

Start the server on this Mac:

```bash
./agent_mcp.py --transport http
```

It serves `http://127.0.0.1:8765/mcp`, listening only on this Mac, and every request
must carry `Authorization: Bearer TOKEN`. On first start the server creates a random
token in `~/.config/agent-mcp/token` (mode 600); set `AGENT_MCP_TOKEN` or
`--token-file` to use another. Requests without the token get 401, and requests whose
Host header is not a local name get 421, which blocks DNS-rebinding attacks from web
pages.

Reach it from another machine without exposing it to the network, for example with
an SSH tunnel from the remote machine:

```bash
ssh -N -L 8765:127.0.0.1:8765 maxim@THIS-MAC
```

The remote client then connects to `http://127.0.0.1:8765/mcp` with the token, for
example from Claude Code:

```bash
claude mcp add --transport http agent-ctl http://127.0.0.1:8765/mcp --header "Authorization: Bearer TOKEN"
```

To listen on a private network address instead, such as a Tailscale IP, pass
`--host` and the name clients will use:

```bash
./agent_mcp.py --transport http --host 100.101.102.103 --allowed-host mymac.tailnet-name.ts.net:8765
```

Anyone who can reach that address and has the token can read your conversations and
send messages as you, so never bind a public interface. The server has no TLS and no
OAuth, so web connectors such as claude.ai's, which need a public HTTPS URL, are not
supported.

## MCP smoke test

```bash
./test/test_mcp_server.py
./test/test_mcp_server.py --app chatgpt --http
```

It starts the server over stdio through the MCP client, checks the tool list and
hints, calls the read-only tools against one app (Cursor by default), including
`status` and `wait_for_reply` on an idle chat, and checks that invalid calls are
refused before any UI action; `ask_agent` is only called with a session that does not
exist, so it fails before sending. `--http` also starts the HTTP
transport on a free port and checks that a missing token, a wrong token and a foreign
Host header are rejected. It never types, sends, answers or switches views. Last
results, 2026-09-25: all checks passed against Cursor (stdio and HTTP) and ChatGPT
(stdio). A real send was also checked through the MCP server in a new ChatGPT chat:
`submit_draft` sent `Reply only: OK`, `status` read busy then idle, and
`wait_for_reply` returned `done` with `OK` after 7 seconds. `ask_agent` itself has only
been run as far as its send step, which then stalled because another script was
polling ChatGPT at the same time; nothing was sent that time.
