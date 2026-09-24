"""One module per desktop app. Each exposes main(args) -> exit status.

VIEWS names each app's two top-level views as (mode argument, `mode` output).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

APPS = ('claude', 'chatgpt', 'cursor')
VIEWS = {
    'claude': (('chat', 'chat'), ('code', 'code')),
    'chatgpt': (('chatgpt', 'chat'), ('codex', 'code')),
    'cursor': (('agents', 'agents'), ('ide', 'ide')),
}


def helper(module, *args):
    """argv and env for running a backend helper module in a child process.

    Helpers run separately so a stuck accessibility call can be killed by timeout.
    """
    env = os.environ.copy()
    env['PYTHONPATH'] = ROOT + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return [sys.executable, '-m', 'backends.' + module, *args], env
