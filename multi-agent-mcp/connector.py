#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=13,<16"]
# ///
"""Connect this Mac's agent_mcp.py to a remote voice server, outbound only.

The connector opens a WebSocket to the server (wss://, or ws:// to this Mac for testing),
authenticates with a device token, starts agent_mcp.py over stdio, and passes MCP
messages (JSON-RPC, one per WebSocket message) between the two. Nothing listens on this
Mac, so no port forwarding or VPN is needed. Each connection gets a fresh MCP server;
when either side ends, the connector starts over after a short delay.

The server can call any tool agent_mcp.py offers, with this Mac's accessibility access.
--read-only refuses tools that submit (send messages, answer questions) here on the Mac,
whatever the server asks. Ctrl-C disconnects.

Sign-in: --login https://SERVER links this Mac to your account on that server, like
`gh auth login`: it shows a one-time code and opens the server's page, where you sign in
and check that the page shows the same code. The server then gives this Mac its own
device token, saved (mode 600) with the server's address, so later runs need no options.
Your account password never reaches the connector.

Token: AGENT_CONNECTOR_TOKEN, or the file given by --token-file
(default ~/.config/agent-mcp/connector-token).

For the Mac app: --python runs agent_mcp.py with that Python (its dependencies installed)
instead of `uv run --script`, and --events prints one JSON object per line instead of text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
TOKEN_FILE = Path.home() / '.config' / 'agent-mcp' / 'connector-token'
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
# Close codes the server uses: the token is refused, this Mac was unlinked, or a newer
# connector of the same account took over (stop), or the session ended (reconnect now).
TOKEN_REFUSED, SESSION_ENDED, REPLACED, UNLINKED = 4001, 4000, 4002, 4003
BACKOFF = (1, 2, 5, 10, 30)
STABLE_SECONDS = 30
EVENTS = False  # --events: JSON lines for the Mac app instead of text


def log(message: str, event: str = 'log', **fields) -> None:
    """Print a line for people, or with --events a JSON object: event, message and fields."""
    if EVENTS:
        print(json.dumps({'event': event, 'message': message, **fields}, ensure_ascii=False), flush=True)
    else:
        print(f'[{time.strftime("%H:%M:%S")}] {message}', flush=True)


class ReadOnlyGuard:
    """Refuses calls to tools the MCP server marks as submitting (destructiveHint)."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.submitting: set[str] = set()

    def learn(self, message: dict) -> None:
        tools = (message.get('result') or {}).get('tools') if isinstance(message.get('result'), dict) else None
        for tool in tools or []:
            if (tool.get('annotations') or {}).get('destructiveHint'):
                self.submitting.add(tool.get('name'))

    def refusal(self, message: dict) -> dict | None:
        if not (self.enabled and message.get('method') == 'tools/call'):
            return None
        name = (message.get('params') or {}).get('name')
        # Before tools/list has been seen, refuse every call rather than guess.
        if name in self.submitting or not self.submitting:
            # A tool-level error (isError), as MCP reports failed tool calls, not a protocol error.
            text = f'{name} was refused: this Mac\'s connector is read-only. Nothing was sent.'
            return {'jsonrpc': '2.0', 'id': message.get('id'),
                    'result': {'content': [{'type': 'text', 'text': text}], 'isError': True,
                               'resultType': 'complete'}}
        return None


async def relay(websocket, command: list[str], guard: ReadOnlyGuard, debug: bool) -> None:
    """Run one MCP server for this connection and pass messages until either side ends."""
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=None if debug else asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)

    async def to_server():
        async for raw in websocket:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                log('Ignored a message that is not JSON.')
                continue
            if not isinstance(message, dict):
                continue
            refusal = guard.refusal(message)
            if refusal:
                name = message['params'].get('name')
                log(f'Refused {name} (read-only).', 'tool', name=name, refused=True)
                await websocket.send(json.dumps(refusal))
                continue
            if message.get('method') == 'tools/call':
                params = message.get('params') or {}
                arguments = params.get('arguments') or {}
                log(f'🛠  {params.get("name")} {json.dumps(arguments, ensure_ascii=False)}', 'tool',
                    name=params.get('name'), arguments=arguments, refused=False)
            # Compact JSON has no raw newlines, so it is exactly one stdio line.
            process.stdin.write(json.dumps(message, ensure_ascii=False).encode() + b'\n')
            await process.stdin.drain()

    async def to_client():
        while line := await process.stdout.readline():
            try:
                guard.learn(json.loads(line))
            except ValueError:
                pass
            await websocket.send(line.decode().strip())

    tasks = [asyncio.create_task(to_server()), asyncio.create_task(to_client())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()


def url_file(token_file: Path) -> Path:
    """The saved server address, next to the token."""
    return token_file.with_name('connector-url')


def post_json(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(data).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def computer_name() -> str:
    try:
        name = subprocess.run(['scutil', '--get', 'ComputerName'], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ''
    return name or os.uname().nodename.split('.')[0] or 'Mac'


def login(server: str, token_file: Path, open_browser: bool) -> str:
    """Link this Mac to an account on the server; save and return its connector URL."""
    parsed = urlparse(server.rstrip('/'))
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in LOCAL_HOSTS):
        fail('--login needs an https:// address (http:// only to this Mac, for testing).', 'usage')
    base = f'{parsed.scheme}://{parsed.netloc}'
    try:
        pairing = post_json(f'{base}/pair/start', {'name': computer_name()})
    except (OSError, ValueError) as error:
        fail(f'Cannot reach {parsed.netloc}: {error}', 'unreachable')
    if EVENTS:
        log(f'Your code: {pairing["user_code"]}', 'code', user_code=pairing['user_code'],
            verification_uri=pairing['verification_uri'])
    else:
        print(f'\n  Your code: {pairing["user_code"]}\n', flush=True)
        print(f'Sign in at {pairing["verification_uri"]}')
        print('and link this Mac only if the page shows the same code.', flush=True)
    if open_browser:
        webbrowser.open(pairing['verification_uri'])
    deadline = time.monotonic() + pairing.get('expires_in', 600)
    while time.monotonic() < deadline:
        time.sleep(pairing.get('interval', 3))
        try:
            result = post_json(f'{base}/pair/poll', {'device_code': pairing['device_code']})
        except (OSError, ValueError) as error:
            log(f'Waiting ({error})')
            continue
        if result.get('status') == 'approved':
            break
        if result.get('status') == 'expired':
            fail('The code expired or was used. Run --login again.', 'expired')
    else:
        fail('The code expired. Run --login again.', 'expired')
    url = f'{"wss" if parsed.scheme == "https" else "ws"}://{parsed.netloc}/connector'
    token_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path, text in ((token_file, result['token']), (url_file(token_file), url)):
        path.touch(mode=0o600)
        path.chmod(0o600)
        path.write_text(text + '\n')
    log(f'This Mac is linked. Token saved to {token_file}.', 'linked')
    return url


def fail(message: str, reason: str):
    """Stop with a message (stderr), or with --events a 'stopped' event and exit status 1."""
    if EVENTS:
        log(message, 'stopped', reason=reason)
        raise SystemExit(1)
    raise SystemExit(message)


async def run(url: str, token: str, command: list[str], read_only: bool, debug: bool) -> int:
    import websockets
    attempt = 0
    while True:
        try:
            async with websockets.connect(url, additional_headers={'Authorization': f'Bearer {token}'},
                                          max_size=16 * 1024 * 1024, ping_interval=20) as websocket:
                log(f'Connected to {urlparse(url).netloc}' + (' (read-only)' if read_only else '') + '.',
                    'connected', server=urlparse(url).netloc, read_only=read_only)
                connected = time.monotonic()
                await relay(websocket, command, ReadOnlyGuard(read_only), debug)
                code = websocket.close_code
                if time.monotonic() - connected > STABLE_SECONDS:
                    attempt = 0  # Back off again only after a connection that lasted.
        except websockets.InvalidStatus as error:
            code = error.response.status_code
            if code in (401, 403):
                log('The server refused this device token. Run with --login to link this Mac again.',
                    'stopped', reason='token_refused')
                return 1
            log(f'The server answered HTTP {code}.')
        except (OSError, websockets.WebSocketException) as error:
            code = None
            log(f'Cannot reach the server: {error}')
        if code == TOKEN_REFUSED:
            log('The server refused this device token. Run with --login to link this Mac again.',
                'stopped', reason='token_refused')
            return 1
        if code == UNLINKED:
            log('This Mac was unlinked from your account. Run with --login to link it again.',
                'stopped', reason='unlinked')
            return 1
        if code == REPLACED:
            # Reconnecting would replace the other connector in turn, and the two would take
            # over from each other forever, dropping every voice session.
            log('Another connector of this account connected, so this one stops.', 'stopped', reason='replaced')
            return 1
        if code == SESSION_ENDED:
            continue  # A fresh MCP server for the next session, at once.
        delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
        attempt += 1
        log(f'Disconnected; reconnecting in {delay} s.', 'disconnected', retry_in=delay)
        await asyncio.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--login', metavar='SERVER',
                        help='Link this Mac to your account on SERVER (such as https://app.mkay.ai), then connect.')
    parser.add_argument('--no-browser', action='store_true', help='With --login, print the page address instead of opening it.')
    parser.add_argument('--url', default=os.environ.get('AGENT_CONNECTOR_URL'),
                        help='Server endpoint, e.g. wss://voice.example.com/connector (or AGENT_CONNECTOR_URL; '
                             'default: saved by --login next to the token).')
    parser.add_argument('--token-file', type=Path, default=TOKEN_FILE, help=f'Default: {TOKEN_FILE}')
    parser.add_argument('--read-only', action='store_true', help='Refuse tools that submit, on this Mac.')
    parser.add_argument('--debug', action='store_true', help="Show the MCP server's own log.")
    parser.add_argument('--python', metavar='PYTHON',
                        help='Run agent_mcp.py with this Python, which has its dependencies installed, '
                             'instead of uv run --script (the Mac app passes its bundled Python).')
    parser.add_argument('--events', action='store_true', help='Print JSON lines (event, message, ...) for the Mac app.')
    args = parser.parse_args()
    global EVENTS
    EVENTS = args.events
    if args.login:
        args.url = login(args.login, args.token_file, not args.no_browser)
    if not args.url:
        try:
            args.url = url_file(args.token_file).read_text().strip()
        except OSError:
            parser.error('--login SERVER (first time) or --url is required')
    parsed = urlparse(args.url)
    if parsed.scheme != 'wss' and not (parsed.scheme == 'ws' and parsed.hostname in LOCAL_HOSTS):
        parser.error('use wss:// (ws:// only to this Mac, for testing)')
    token = os.environ.get('AGENT_CONNECTOR_TOKEN', '').strip()
    if not token:
        try:
            token = args.token_file.read_text().strip()
        except OSError:
            parser.error(f'no device token: set AGENT_CONNECTOR_TOKEN or write it to {args.token_file}')
    server = os.environ.get('AGENT_MCP') or str(HERE / 'agent_mcp.py')
    if args.python:
        command = [args.python, server]
    else:
        uv = shutil.which('uv') or '/opt/homebrew/bin/uv'
        command = [uv, 'run', '--script', server]

    loop = asyncio.new_event_loop()
    task = loop.create_task(run(args.url, token, command, args.read_only, args.debug))
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        return loop.run_until_complete(task)
    except asyncio.CancelledError:
        log('Disconnected.', 'stopped', reason='interrupted')
        return 130


if __name__ == '__main__':
    sys.exit(main())
