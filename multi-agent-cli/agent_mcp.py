#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.2,<3", "uvicorn>=0.30"]
# ///
"""MCP server exposing agent_ctl.py: control Claude, ChatGPT/Codex and Cursor desktop apps.

Every tool runs the tested CLI (agent_ctl.py) as a child process, one at a time,
so MCP clients get exactly the CLI's behavior, safety checks and timeouts.

Transports:
  stdio  For MCP clients on this Mac (Claude Desktop, Claude Code, Cursor).
  http   Streamable HTTP for remote clients. Binds 127.0.0.1 by default and always
         requires a bearer token; reach it remotely through an SSH tunnel or Tailscale.

The process that launches this server needs macOS Accessibility permission.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent
CLI = ROOT / 'agent_ctl.py'
sys.path.insert(0, str(ROOT))
from backends import APPS, VIEWS  # noqa: E402

# A CLI command has its own internal limits; this only stops a stuck child process.
COMMAND_TIMEOUT = 150
DEFAULT_TOKEN_FILE = Path.home() / '.config' / 'agent-mcp' / 'token'

App = Literal['claude', 'chatgpt', 'cursor']

INSTRUCTIONS = """\
Controls the Claude, ChatGPT/Codex and Cursor desktop apps on the user's Mac through their
user interfaces. Each app has two views (see list_apps); listings depend on the current view.

Typical flow: get_mode -> set_mode if needed -> list_projects / list_sessions -> open_session
-> read_reply. To talk to an agent: send_message, then call read_reply again later; sending
does not wait for the reply, and read_reply may return a partial reply while it streams.

Cursor agents sometimes stop on a multiple-choice question: read_reply then returns
pending_question, send_message and submit_draft refuse, and cursor_answer_question answers it.

Tools act on the real apps: they bring the app to the front and click, paste and press Return.
Calls run one at a time. When a call fails, its error text says what happened; if a send or
answer could not be confirmed, read_reply before retrying so a message is not sent twice.
"""

mcp = MCPServer(name='agent-ctl', instructions=INSTRUCTIONS)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
NAVIGATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                           open_world_hint=False)
DRAFT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                        open_world_hint=False)
# Submitting cannot be undone: it hands a message or an answer to an agent that then acts on it.
SUBMIT = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                         open_world_hint=True)

_lock = asyncio.Lock()  # The apps have one UI each: never drive them concurrently.


async def run_cli(app: str, *args: str) -> str:
    """Run one agent_ctl.py command and return its stdout; raise ToolError on failure."""
    command = [sys.executable, str(CLI), '--app', app, *args]
    async with _lock:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ToolError(f'{" ".join(args)} timed out after {COMMAND_TIMEOUT}s and was stopped. '
                            'Check the app before retrying; nothing was retried.')
    out = stdout.decode(errors='replace').strip()
    err = stderr.decode(errors='replace').strip()
    if process.returncode:
        raise ToolError((err or out or 'Command failed.') + f' (exit status {process.returncode})')
    return out


def lines(text: str) -> list[str]:
    # Identical titles can be separate sessions: never deduplicate.
    return [line for line in text.splitlines() if line.strip()]


def check_mode(app: str, mode: str) -> str:
    names = {name for pair in VIEWS[app] for name in pair}
    if mode.lower() not in names:
        raise ToolError(f'{app} views are ' + ', '.join(sorted(names)) + '.')
    return mode.lower()


# Results


class AppInfo(BaseModel):
    app: str
    views: list[str]
    extra_tools: list[str]


class Mode(BaseModel):
    app: str
    mode: str
    message: str | None = None


class Projects(BaseModel):
    app: str
    projects: list[str]


class Sessions(BaseModel):
    app: str
    project: str | None = None
    recents: bool = False
    sessions: list[str]


class Result(BaseModel):
    app: str
    message: str


class Option(BaseModel):
    letter: str
    text: str


class Question(BaseModel):
    prompt: str
    options: list[Option]


class Reply(BaseModel):
    app: str
    reply: str
    pending_question: Question | None = None


class Answer(BaseModel):
    status: Literal['done', 'next_question']
    question: Question | None = None


class Diagnostic(BaseModel):
    app: str
    output: str


PENDING = 'Pending question: '


def split_question(text: str) -> tuple[str, Question | None]:
    """Split the CLI's `Pending question:` block (Cursor) from the text before it."""
    all_lines = text.splitlines()
    start = next((i for i, line in enumerate(all_lines) if line.startswith(PENDING)), None)
    if start is None:
        return text, None
    options = []
    for line in all_lines[start + 1:]:
        match = re.fullmatch(r'\s+([A-Z])\. (.*)', line)
        if match:
            options.append(Option(letter=match[1], text=match[2]))
    question = Question(prompt=all_lines[start][len(PENDING):], options=options)
    return '\n'.join(all_lines[:start]).rstrip(), question


# Tools


@mcp.tool(annotations=READ)
def list_apps() -> list[AppInfo]:
    """List the controllable apps, the view names each accepts in set_mode, and app-only tools."""
    extra = {'cursor': ['cursor_open_project', 'cursor_answer_question']}
    return [AppInfo(app=app, views=[name for name, _ in VIEWS[app]], extra_tools=extra.get(app, []))
            for app in APPS]


@mcp.tool(annotations=READ)
async def get_mode(app: App) -> Mode:
    """Return the app's current view: claude chat|code, chatgpt chat|code (ChatGPT|Codex), cursor agents|ide."""
    return Mode(app=app, mode=await run_cli(app, 'mode'))


@mcp.tool(annotations=NAVIGATE)
async def set_mode(app: App, mode: str) -> Mode:
    """Switch the app's view and confirm it.

    Views: claude chat|code; chatgpt chatgpt|codex (also chat|code); cursor agents|ide.
    For Cursor, "ide" lets Cursor pick the IDE window; use cursor_open_project for a specific one.
    """
    message = await run_cli(app, 'mode', check_mode(app, mode))
    return Mode(app=app, mode=await run_cli(app, 'mode'), message=message)


@mcp.tool(annotations=READ)
async def list_projects(app: App) -> Projects:
    """List projects in the current view.

    Claude Chat: Projects page (opens it). Claude Code: folders. ChatGPT/Codex: sidebar projects.
    Cursor Agents: sidebar repositories. Cursor IDE: open workspace windows.
    """
    return Projects(app=app, projects=lines(await run_cli(app, 'projects')))


@mcp.tool(annotations=READ)
async def list_sessions(app: App, project: str | None = None, recents: bool = False) -> Sessions:
    """List session titles in the current view, optionally for one project or only Recents.

    project and recents cannot be combined; Cursor has no Recents. Listing may expand
    collapsed sidebar sections. Titles are exactly as shown, duplicates included.
    """
    if project and recents:
        raise ToolError('Pass either project or recents, not both.')
    args = ['sessions'] + (['--project', project] if project else ['--recents'] if recents else [])
    out = lines(await run_cli(app, *args))
    if project:
        out = [line[2:] if line.startswith('  ') else line for line in out[1:]]
    return Sessions(app=app, project=project, recents=recents, sessions=out)


@mcp.tool(annotations=NAVIGATE)
async def open_session(app: App, title: str) -> Result:
    """Open a session by title in the current view (Claude: first title containing it;
    ChatGPT and Cursor: exact title, else a unique substring). Needed before reading or sending."""
    return Result(app=app, message=await run_cli(app, 'session', title))


@mcp.tool(annotations=READ)
async def read_reply(app: App) -> Reply:
    """Read the latest assistant reply in the open conversation.

    May be partial while the reply streams. For Cursor, includes everything after the latest
    user message (tool summaries too) and any pending multiple-choice question.
    """
    reply, question = split_question(await run_cli(app, 'read'))
    return Reply(app=app, reply=reply, pending_question=question)


@mcp.tool(annotations=DRAFT)
async def type_text(app: App, text: str) -> Result:
    """Paste text into the empty prompt box without submitting (refuses if a draft exists)."""
    return Result(app=app, message=await run_cli(app, 'type', text))


@mcp.tool(annotations=SUBMIT)
async def send_message(app: App, text: str) -> Result:
    """Paste text into the empty prompt box, verify it, and press Return to send it.

    Refuses if a draft exists (or, in Cursor, while a question is pending). Does not wait for
    the reply: call read_reply afterwards. If sending cannot be confirmed, read_reply first
    instead of retrying, so the message is not sent twice.
    """
    return Result(app=app, message=await run_cli(app, 'send', text))


@mcp.tool(annotations=SUBMIT)
async def submit_draft(app: App) -> Result:
    """Press Return to send the draft already in the prompt box (refuses an empty prompt)."""
    return Result(app=app, message=await run_cli(app, 'enter'))


@mcp.tool(annotations=NAVIGATE)
async def cursor_open_project(name: str) -> Result:
    """Cursor only: bring an open IDE workspace window forward (switches to the IDE view).
    Workspace names come from list_projects in the IDE view."""
    return Result(app='cursor', message=await run_cli('cursor', 'project', name))


@mcp.tool(annotations=SUBMIT)
async def cursor_answer_question(letter: str, text: str | None = None) -> Answer:
    """Cursor only: answer the pending multiple-choice question shown by read_reply.

    Pass text only for a free-text option such as "Other". Returns status "done" when the
    question panel closes, or "next_question" with the next question when Cursor asks several.
    """
    if not re.fullmatch(r'[A-Za-z]', letter):
        raise ToolError('letter must be a single option letter such as "B".')
    out = await run_cli('cursor', 'answer', letter.upper(), *([text] if text else []))
    if out.strip() == 'done':
        return Answer(status='done')
    _, question = split_question(out)
    if question is None:
        raise ToolError('Unexpected answer output: ' + out)
    return Answer(status='next_question', question=question)


@mcp.tool(annotations=READ)
async def diagnose(app: App, kind: Literal['mode', 'sidebar']) -> Diagnostic:
    """Read-only diagnostic of the app's mode controls or sidebar, for when a tool fails after an
    app update. Can include conversation titles."""
    return Diagnostic(app=app, output=await run_cli(app, 'debug-' + kind))


# HTTP transport


class BearerAuth:
    """ASGI middleware: reject HTTP requests without the exact bearer token."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, ('Bearer ' + token).encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            supplied = dict(scope['headers']).get(b'authorization', b'')
            if not hmac.compare_digest(supplied, self.expected):
                await send({'type': 'http.response.start', 'status': 401,
                            'headers': [(b'content-type', b'application/json'),
                                        (b'www-authenticate', b'Bearer')]})
                await send({'type': 'http.response.body', 'body': b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def load_token(path: Path) -> str:
    """AGENT_MCP_TOKEN, else the token file, created with a random token on first use."""
    if os.environ.get('AGENT_MCP_TOKEN'):
        return os.environ['AGENT_MCP_TOKEN']
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(secrets.token_urlsafe(32) + '\n')
        path.chmod(0o600)
        print(f'Created a new bearer token in {path}', file=sys.stderr)
    token = path.read_text().strip()
    if len(token) < 16:
        raise SystemExit(f'Token in {path} is shorter than 16 characters.')
    return token


def serve_http(host: str, port: int, token_file: Path, allowed_hosts: list[str]) -> None:
    import uvicorn

    # Host/Origin checks stay on for every bind address (DNS-rebinding protection);
    # the bind address itself and any --allowed-host names are accepted.
    local = host in ('127.0.0.1', 'localhost', '::1')
    extra = list(allowed_hosts) + ([] if local else [f'[{host}]:{port}' if ':' in host else f'{host}:{port}'])
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=['127.0.0.1:*', 'localhost:*', '[::1]:*', *extra],
        allowed_origins=['http://127.0.0.1:*', 'http://localhost:*', 'http://[::1]:*']
        + [f'{scheme}://{h}' for h in extra for scheme in ('http', 'https')])
    app = mcp.streamable_http_app(host=host, transport_security=security)
    if not local:
        print(f'Warning: listening on {host}; anyone who can reach it and has the token can '
              'control your desktop apps.', file=sys.stderr)
    print(f'agent-ctl MCP server: http://{host}:{port}/mcp (bearer token from {token_file} '
          'or AGENT_MCP_TOKEN)', file=sys.stderr)
    uvicorn.run(BearerAuth(app, load_token(token_file)), host=host, port=port, log_level='warning')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--transport', choices=('stdio', 'http'), default='stdio')
    parser.add_argument('--host', default='127.0.0.1', help='HTTP bind address (default 127.0.0.1).')
    parser.add_argument('--port', type=int, default=8765, help='HTTP port (default 8765).')
    parser.add_argument('--token-file', type=Path, default=DEFAULT_TOKEN_FILE,
                        help=f'Bearer token file for HTTP (default {DEFAULT_TOKEN_FILE}).')
    parser.add_argument('--allowed-host', action='append', default=[],
                        help='Extra Host header value accepted over HTTP, e.g. mymac.tailnet.ts.net:8765 '
                             '(repeatable). Needed when clients reach the server by another name.')
    args = parser.parse_args()
    if args.transport == 'stdio':
        mcp.run('stdio')
    else:
        serve_http(args.host, args.port, args.token_file.expanduser(), args.allowed_host)


if __name__ == '__main__':
    main()
