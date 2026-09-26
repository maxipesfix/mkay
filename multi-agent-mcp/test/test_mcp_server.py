#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.2,<3", "uvicorn>=0.30"]
# ///
"""Live MCP smoke test: start agent_mcp.py over stdio (and optionally HTTP) and call its tools.

Only read-only tools run against the app (default Cursor), plus calls the server must refuse
without touching the app. Nothing is typed, sent, answered or switched.

  ./test/test_mcp_server.py                 # stdio, Cursor
  ./test/test_mcp_server.py --app chatgpt
  ./test/test_mcp_server.py --http          # also start the HTTP transport and check the token
"""
import argparse
import asyncio
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / 'agent_mcp.py'
EXPECTED_TOOLS = {'list_apps', 'status', 'get_mode', 'set_mode', 'list_projects', 'list_sessions',
                  'open_session', 'new_chat', 'read_reply', 'wait_for_reply', 'ask_agent', 'type_text', 'send_message',
                  'submit_draft', 'cursor_open_project', 'cursor_answer_question', 'diagnose'}

failures = []


def check(condition, message):
    print(('PASS: ' if condition else 'FAIL: ') + message, flush=True)
    if not condition:
        failures.append(message)


async def exercise(client, app):
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    check(set(tools) == EXPECTED_TOOLS, 'tool list: ' + ', '.join(sorted(tools)))
    check(tools['read_reply'].annotations.read_only_hint is True, 'read_reply is marked read-only')
    check(tools['send_message'].annotations.destructive_hint is True, 'send_message is marked as submitting')
    check(tools['ask_agent'].annotations.destructive_hint is True, 'ask_agent is marked as submitting')

    apps = await client.call_tool('list_apps', {})
    names = [entry['app'] for entry in apps.structured_content['result']]
    check(names == ['claude', 'chatgpt', 'cursor'], f'list_apps: {names}')

    mode = await client.call_tool('get_mode', {'app': app})
    check(not mode.is_error, f'get_mode({app}): {mode.structured_content}')
    projects = await client.call_tool('list_projects', {'app': app})
    listed = (projects.structured_content or {}).get('projects', [])
    check(not projects.is_error, f'list_projects({app}): {len(listed)} projects')
    if listed:
        sessions = await client.call_tool('list_sessions', {'app': app, 'project': listed[0]})
        got = (sessions.structured_content or {}).get('sessions')
        check(not sessions.is_error and isinstance(got, list), f'list_sessions({app}, {listed[0]!r}): {got}')
    state = await client.call_tool('status', {'apps': [app]})
    entry = (state.structured_content or {}).get('result', [{}])[0]
    check(not state.is_error and entry.get('mode') and entry.get('error') is None, f'status([{app}]): {entry}')
    if entry.get('busy') is False and not entry.get('pending_question'):
        waited = await client.call_tool('wait_for_reply', {'app': app, 'timeout_seconds': 60})
        result = waited.structured_content or {}
        if waited.is_error:
            # Idle with no conversation open: must fail fast with the reason, not time out.
            check('Nothing to wait for' in waited.content[0].text,
                  f'wait_for_reply({app}) without a conversation reports it: {waited.content[0].text[:100]}')
        else:
            check(result.get('status') == 'done',
                  f"wait_for_reply({app}) on an idle chat: {result.get('status')} after {result.get('waited_seconds')}s")
    else:
        print(f'SKIP: wait_for_reply({app}) needs an idle chat without a pending question', flush=True)
    reply = await client.call_tool('read_reply', {'app': app})
    text = reply.content[0].text if reply.content else ''
    # An empty or missing conversation is a clean error ("No ... conversation", "Cannot identify ... reply").
    clean = not reply.is_error or any(k in text for k in ('No ', 'Cannot identify', 'Nothing'))
    check(clean, f'read_reply({app}) returned or reported cleanly: {text[:80]!r}')

    # Refusals: rejected before any UI action.
    bad_mode = await client.call_tool('set_mode', {'app': app, 'mode': 'nonsense'})
    check(bad_mode.is_error, 'set_mode refuses an unknown view: ' + bad_mode.content[0].text)
    both = await client.call_tool('list_sessions', {'app': app, 'project': 'x', 'recents': True})
    check(both.is_error, 'list_sessions refuses project together with recents')
    bad_letter = await client.call_tool('cursor_answer_question', {'letter': '12'})
    check(bad_letter.is_error, 'cursor_answer_question refuses a non-letter')
    missing = await client.call_tool('ask_agent', {'app': app, 'message': 'must not be sent',
                                                   'session': 'no-such-session-title-xyz'})
    check(missing.is_error, 'ask_agent stops at an unknown session before sending: ' + missing.content[0].text[:90])
    both_targets = await client.call_tool('ask_agent', {'app': app, 'message': 'must not be sent',
                                                        'session': 'x', 'new_chat': True})
    check(both_targets.is_error, 'ask_agent refuses session together with new_chat')
    cursor_new = await client.call_tool('new_chat', {'app': 'cursor'})
    check(cursor_new.is_error, 'new_chat is refused for apps without support: ' + cursor_new.content[0].text[:80])
    bad_app = await client.call_tool('get_mode', {'app': 'codex'})
    check(bad_app.is_error, 'get_mode refuses an unknown app')
    if app == 'cursor':
        recents = await client.call_tool('list_sessions', {'app': 'cursor', 'recents': True})
        check(recents.is_error and 'exit status 2' in recents.content[0].text,
              'Cursor Recents is refused with the CLI message: ' + recents.content[0].text)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def exercise_http(app):
    token, port = secrets.token_urlsafe(24), free_port()
    env = dict(os.environ, AGENT_MCP_TOKEN=token)
    server = subprocess.Popen([sys.executable, str(SERVER), '--transport', 'http', '--port', str(port)],
                              env=env, stderr=subprocess.PIPE, text=True)
    url = f'http://127.0.0.1:{port}/mcp'
    try:
        async with httpx2.AsyncClient() as http:
            for _ in range(50):
                try:
                    anonymous = await http.post(url, json={})
                    break
                except httpx2.ConnectError:
                    await asyncio.sleep(0.2)
            check(anonymous.status_code == 401, f'HTTP without a token is rejected ({anonymous.status_code})')
            wrong = await http.post(url, json={}, headers={'Authorization': 'Bearer wrong-token-value'})
            check(wrong.status_code == 401, f'HTTP with a wrong token is rejected ({wrong.status_code})')
            forged = await http.post(url, json={}, headers={'Authorization': f'Bearer {token}', 'Host': 'evil.example'})
            check(forged.status_code in (400, 403, 421), f'HTTP with a foreign Host header is rejected ({forged.status_code})')
        async with httpx2.AsyncClient(headers={'Authorization': f'Bearer {token}'}, timeout=200) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                tools = (await client.list_tools()).tools
                check(len(tools) == len(EXPECTED_TOOLS), f'HTTP with the token lists {len(tools)} tools')
                mode = await client.call_tool('get_mode', {'app': app})
                check(not mode.is_error, f'HTTP get_mode({app}): {mode.structured_content}')
    finally:
        server.terminate()
        server.wait(10)


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--app', choices=('claude', 'chatgpt', 'cursor'), default='cursor')
    parser.add_argument('--http', action='store_true', help='Also test the HTTP transport and token check.')
    args = parser.parse_args()
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with Client(params, read_timeout_seconds=200) as client:
        print('== stdio', flush=True)
        await exercise(client, args.app)
    if args.http:
        print('== http', flush=True)
        await exercise_http(args.app)
    print(f'{len(failures)} failures.' if failures else 'RESULT: PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
