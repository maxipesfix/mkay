#!/usr/bin/env python3
"""Control AI desktop apps on macOS: ./agent_ctl.py [--app claude|chatgpt|cursor] COMMAND ..."""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from backends import APPS, VIEWS  # noqa: E402


def usage():
    views = '\n'.join(f'  {app:8} mode ' + '|'.join(name for name, _ in VIEWS[app]) for app in APPS)
    print(f"""Usage: ./agent_ctl.py [--app {'|'.join(APPS)}] COMMAND [ARGS]
Claude is the default app. Put --app before the command.

Views:
{views}

Commands: mode [VIEW], status, projects, sessions [--project NAME | --recents],
          session "title", read, type "text", send "text", enter, debug-mode, debug-sidebar
Run ./agent_ctl.py --app APP help for app-specific commands and diagnostics.
./agent_ctl.py apps --json prints the apps and their views as JSON (for programs).""")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    app = 'claude'
    if args[:1] == ['--app']:
        if len(args) < 2:
            print('--app requires ' + ' or '.join(APPS), file=sys.stderr)
            return 2
        app = args[1].lower()
        if app not in APPS:
            print('--app must be ' + ', '.join(APPS) + '. For Codex use --app chatgpt, then mode codex.', file=sys.stderr)
            return 2
        args = args[2:]
    elif args[:1] in (['-h'], ['--help']):
        usage()
        return 0
    elif args == ['apps', '--json']:
        # Machine-readable app list: each app's views as [mode argument, `mode` output] pairs.
        print(json.dumps({'apps': [{'app': app, 'views': [list(pair) for pair in VIEWS[app]]}
                                   for app in APPS]}))
        return 0
    backend = importlib.import_module('backends.' + app)
    return backend.main(args) or 0


if __name__ == '__main__':
    sys.exit(main())
