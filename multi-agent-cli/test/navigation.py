"""Shared live CLI test: switch both views and enumerate each view's sidebar.

Run from a terminal with Accessibility access. Leave the app untouched until
the test finishes. The test switches modes, opens project pages, expands lists, and reads names.
It does not type or submit messages.
"""
import argparse
from datetime import datetime
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backends import APPS, VIEWS  # noqa: E402

# Apps whose unfiltered `sessions` command is exercised separately.
UNFILTERED = {'claude', 'cursor'}
# Apps without a Recents list; `sessions --recents` is expected to be refused.
NO_RECENTS = {'cursor'}


class NavigationTest:
    def __init__(self, cli, log, run=subprocess.run, app='chatgpt'):
        if app not in APPS:
            raise ValueError('app must be one of ' + ', '.join(APPS))
        self.app = app
        self.cli, self.log, self.run = str(cli), log, run
        self.failures = []
        self.summaries = []
        self.commands = 0

    def fail(self, message):
        self.failures.append(message)
        self.log('FAIL: ' + message)

    def command(self, *args):
        argv = [sys.executable, self.cli, '--app', self.app, *args]
        self.log('\n$ ' + shlex.join(argv))
        self.commands += 1
        started = time.monotonic()
        try:
            result = self.run(argv, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired as error:
            for title, data in (('STDOUT', error.stdout), ('STDERR', error.stderr)):
                if data:
                    text = data.decode(errors='replace') if isinstance(data, bytes) else data
                    self.log(title + ' (partial):\n' + text.rstrip('\n'))
            self.fail(shlex.join(args) + ' timed out after 90 seconds; not retried.')
            return None
        except OSError as error:
            self.fail(shlex.join(args) + ': ' + str(error))
            return None
        self.log('STDOUT:\n' + (result.stdout.rstrip('\n') or '(empty)'))
        self.log('STDERR:\n' + (result.stderr.rstrip('\n') or '(empty)'))
        self.log(f'Exit: {result.returncode}; elapsed: {time.monotonic() - started:.1f}s')
        if result.returncode:
            self.fail(shlex.join(args) + f' exited {result.returncode}.')
            return None
        return result.stdout

    def verify_mode(self, expected):
        actual = self.command('mode')
        if actual is None:
            return False
        if actual.strip() != expected:
            self.fail(f'Expected mode {expected!r}; read {actual.strip()!r}.')
            return False
        self.log('PASS: active mode is ' + expected)
        return True

    @staticmethod
    def titles(output):
        # Identical titles can be separate sessions. Never deduplicate them.
        return [line for line in output.splitlines() if line.strip()]

    def view(self, name, expected):
        self.log('\n===== ' + name.upper() + ' =====')
        start_failures = len(self.failures)
        summary = {'mode': name, 'projects': None, 'sessions': [], 'recents': None, 'passed': False}
        self.summaries.append(summary)
        if self.command('mode', name) is None or not self.verify_mode(expected):
            self.log('SKIP: this view was not verified; its lists would be unreliable.')
            return
        if self.app in UNFILTERED:
            # Exercise the unfiltered sessions command separately.
            # It is not a substitute for projects or Recents.
            output = self.command('sessions')
            summary['all_sessions'] = self.titles(output) if output is not None else None
        project_output = self.command('projects')
        if project_output is not None:
            projects = self.titles(project_output)
            summary['projects'] = projects
            for project in projects:
                output = self.command('sessions', '--project', project)
                sessions = None
                if output is not None:
                    lines = output.splitlines()
                    if not lines or lines[0] != '[' + project + ']':
                        self.fail('Unexpected project-session heading for ' + repr(project))
                    elif any(line and not line.startswith('  ') for line in lines[1:]):
                        self.fail('Unexpected project-session output for ' + repr(project))
                    else:
                        sessions = [line[2:] for line in lines[1:] if line.strip()]
                summary['sessions'].append((project, sessions))
        if self.app not in NO_RECENTS:
            recent_output = self.command('sessions', '--recents')
            if recent_output is not None:
                summary['recents'] = self.titles(recent_output)
        self.verify_mode(expected)
        summary['passed'] = len(self.failures) == start_failures

    def print_summary(self):
        self.log('\n===== SUMMARY =====')
        for summary in self.summaries:
            status = 'PASS' if summary['passed'] else 'FAIL'
            projects = len(summary['projects']) if summary['projects'] is not None else 'not read'
            recents = (len(summary['recents']) if summary['recents'] is not None
                       else 'not supported' if self.app in NO_RECENTS else 'not read')
            self.log('')
            self.log(f'{summary["mode"]}: {status}; projects={projects}; recent sessions={recents}')
            if self.app in UNFILTERED:
                self.log('  Unfiltered sessions:')
                sessions = summary.get('all_sessions')
                if sessions is None:
                    self.log('    (not read)')
                elif not sessions:
                    self.log('    (none returned)')
                else:
                    for index, title in enumerate(sessions, 1):
                        self.log(f'    {index}. {title}')
            self.log('  Projects:')
            if summary['projects'] is None:
                self.log('    (not read)')
            elif not summary['projects']:
                self.log('    (none returned)')
            for project, sessions in summary['sessions']:
                count = len(sessions) if sessions is not None else 'FAILED'
                self.log(f'    {project}: {count} sessions')
                if sessions is None:
                    self.log('      (session names not read)')
                elif not sessions:
                    self.log('      (none returned)')
                else:
                    for index, title in enumerate(sessions, 1):
                        self.log(f'      {index}. {title}')
            if self.app in NO_RECENTS:
                continue
            self.log('  Recent sessions:')
            if summary['recents'] is None:
                self.log('    (not read)')
            elif not summary['recents']:
                self.log('    (none returned)')
            else:
                for index, title in enumerate(summary['recents'], 1):
                    self.log(f'    {index}. {title}')
        self.log(f'{self.commands} CLI commands; {len(self.failures)} failures.')
        for failure in self.failures:
            self.log('  FAIL: ' + failure)
        self.log('Names and counts are exactly what the CLI returned; duplicates are preserved.')
        self.log('PASS confirms command execution and mode checks, not visual accuracy or complete account history.')

    def execute(self):
        views = VIEWS[self.app]
        initial = self.command('mode')
        if initial is None or initial.strip() not in [expected for _, expected in views]:
            if initial is not None:
                self.fail('Cannot recognize the starting mode: ' + repr(initial.strip()))
            return False
        initial = initial.strip()
        for name, expected in views:
            self.view(name, expected)
        self.log('\n===== RESTORE STARTING MODE =====')
        restore = next(name for name, expected in views if expected == initial)
        if self.command('mode', restore) is not None:
            self.verify_mode(initial)
        self.print_summary()
        return not self.failures


def main(argv=None, default_app='claude'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, default=ROOT / 'agent_ctl.py',
                        help='CLI entry point (default: ../agent_ctl.py relative to this test).')
    parser.add_argument('--app', choices=APPS, default=default_app)
    parser.add_argument('--report', type=Path, help='New transcript file; an existing file is never overwritten.')
    args = parser.parse_args(argv)
    cli = args.cli.expanduser().resolve()
    if not cli.is_file():
        parser.error('CLI not found: ' + str(cli))
    report = args.report or Path.cwd() / (args.app + '-navigation-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.txt')
    report = report.expanduser().resolve()
    try:
        with report.open('x', encoding='utf-8') as transcript:
            def log(message):
                print(message, flush=True)
                transcript.write(message + '\n')
                transcript.flush()
            app_label = {'claude': 'Claude Chat/Code', 'chatgpt': 'ChatGPT/Codex', 'cursor': 'Cursor Agents/IDE'}[args.app]
            log('Live ' + app_label + ' navigation test — ' + datetime.now().astimezone().isoformat())
            log('Report: ' + str(report))
            log('Leave the app untouched. Each command has a 90-second limit; the full test may take several minutes.')
            test = NavigationTest(cli, log, app=args.app)
            try:
                passed = test.execute()
            except KeyboardInterrupt:
                log('\nINTERRUPTED: stopped without further UI actions. The starting mode may not be restored.')
                return 130
            log('RESULT: ' + ('PASS' if passed else 'FAIL'))
            log('Saved report: ' + str(report))
            return 0 if passed else 1
    except OSError as error:
        print('Report error: ' + str(error), file=sys.stderr)
        return 1

