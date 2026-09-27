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

Token: AGENT_CONNECTOR_TOKEN, or the file given by --token-file
(default ~/.config/agent-mcp/connector-token).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
TOKEN_FILE = Path.home() / '.config' / 'agent-mcp' / 'connector-token'
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
# Close codes the server uses: the token is refused (stop), or the session ended (reconnect now).
TOKEN_REFUSED, SESSION_ENDED = 4001, 4000
BACKOFF = (1, 2, 5, 10, 30)
STABLE_SECONDS = 30


def log(message: str) -> None:
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
                log(f'Refused {message["params"].get("name")} (read-only).')
                await websocket.send(json.dumps(refusal))
                continue
            if message.get('method') == 'tools/call':
                params = message.get('params') or {}
                log(f'🛠  {params.get("name")} {json.dumps(params.get("arguments") or {}, ensure_ascii=False)}')
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


async def run(url: str, token: str, command: list[str], read_only: bool, debug: bool) -> int:
    import websockets
    attempt = 0
    while True:
        try:
            async with websockets.connect(url, additional_headers={'Authorization': f'Bearer {token}'},
                                          max_size=16 * 1024 * 1024, ping_interval=20) as websocket:
                log(f'Connected to {urlparse(url).netloc}' + (' (read-only)' if read_only else '') + '.')
                connected = time.monotonic()
                await relay(websocket, command, ReadOnlyGuard(read_only), debug)
                code = websocket.close_code
                if time.monotonic() - connected > STABLE_SECONDS:
                    attempt = 0  # Back off again only after a connection that lasted.
        except websockets.InvalidStatus as error:
            code = error.response.status_code
            if code in (401, 403):
                log('The server refused this device token. Pair this Mac again to get a new one.')
                return 1
            log(f'The server answered HTTP {code}.')
        except (OSError, websockets.WebSocketException) as error:
            code = None
            log(f'Cannot reach the server: {error}')
        if code == TOKEN_REFUSED:
            log('The server refused this device token. Pair this Mac again to get a new one.')
            return 1
        if code == SESSION_ENDED:
            continue  # A fresh MCP server for the next session, at once.
        delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
        attempt += 1
        log(f'Disconnected; reconnecting in {delay} s.')
        await asyncio.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=os.environ.get('AGENT_CONNECTOR_URL'),
                        help='Server endpoint, e.g. wss://voice.example.com/connector (or AGENT_CONNECTOR_URL).')
    parser.add_argument('--token-file', type=Path, default=TOKEN_FILE, help=f'Default: {TOKEN_FILE}')
    parser.add_argument('--read-only', action='store_true', help='Refuse tools that submit, on this Mac.')
    parser.add_argument('--debug', action='store_true', help="Show the MCP server's own log.")
    args = parser.parse_args()
    if not args.url:
        parser.error('--url (or AGENT_CONNECTOR_URL) is required')
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
    uv = shutil.which('uv') or '/opt/homebrew/bin/uv'
    command = [uv, 'run', '--script', server]

    loop = asyncio.new_event_loop()
    task = loop.create_task(run(args.url, token, command, args.read_only, args.debug))
    loop.add_signal_handler(signal.SIGINT, task.cancel)
    try:
        return loop.run_until_complete(task)
    except asyncio.CancelledError:
        log('Disconnected.')
        return 130


if __name__ == '__main__':
    sys.exit(main())
