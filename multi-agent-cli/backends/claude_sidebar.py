"""Claude navigation through named native accessibility controls, without coordinates.

Chat projects come from the Projects page, project sessions from its Recents
table, and recent sessions from Chats and tasks in the sidebar. Code projects
are folder groups; its unassigned sessions live in No folder. Session titles
come from matched row/menu pairs, never from arbitrary button labels.
"""
from dataclasses import dataclass
import os
import re
import subprocess
import sys
import time

from ax_native import AXError, Changed, NativeAX, SidebarError, expanded, label
from backends.chatgpt_sidebar import Sidebar


@dataclass
class Item:
    title: str
    node: object
    container: object = None


class ClaudeSidebar(Sidebar):
    # status walks the whole primary pane, which holds the open conversation.
    STACK_BUDGET = 20000
    def main_window(self):
        windows = [w for w in self.app.get('AXWindows') or [] if w.get('AXTitle') == 'Claude']
        if len(windows) != 1:
            raise SidebarError('Cannot identify one Claude main window.')
        return windows[0]

    def region(self, description):
        for node, role, _ in self.walk(self.main_window(), 6000):
            if role == 'AXGroup' and node.get('AXDescription') == description:
                return node
        raise Changed('Claude region is not exposed: ' + description)

    def rows(self, root):
        return list(self.walk(root, 6000))

    def mode(self):
        modes = []
        for node, role, _ in self.rows(self.region('Sidebar')):
            if role != 'AXRadioButton' or node.get('AXValue') not in (1, True, '1'):
                continue
            description = node.get('AXDescription') or ''
            if description.startswith('Chat and Cowork'):
                modes.append('chat')
            elif description == 'Code' or description.startswith('Code,'):
                modes.append('code')
        return self.unique(modes, 'selected Claude mode')

    def named_button(self, root, text, attribute=None):
        matches = [node for node, role, _ in self.rows(root)
                   if role == 'AXButton' and (node.get(attribute) if attribute else label(node)) == text]
        return self.unique(matches, 'Claude button ' + repr(text))

    def sidebar_sessions(self, root):
        """Identify the opening control paired with each row's options menu.

        This includes unread, running, error and merged rows, without mistaking
        their status labels or nested Mark as read buttons for session titles.
        """
        result, seen = [], set()
        for menu, role, ancestors in self.rows(root):
            description = menu.get('AXDescription') if role == 'AXPopUpButton' else None
            if not description or not description.startswith('More options for '):
                continue
            title = description.removeprefix('More options for ')
            if not title:
                raise Changed('Claude exposed a session menu without a title.')
            candidates = []
            for parent in reversed(ancestors[-3:]):
                for child in parent.get('AXChildren') or []:
                    if child.get('AXRole') not in ('AXButton', 'AXLink'):
                        continue
                    text = label(child)
                    if text == title or text.endswith(' ' + title):
                        candidates.append(child)
                if candidates:
                    break
            if not candidates:
                raise Changed('No opening control matched session menu: ' + title)
            node = self.unique(candidates, 'session row for ' + repr(title))
            if node not in seen:
                seen.add(node)
                result.append(Item(title, node))
        return result

    def folders(self):
        result = []
        for node, role, ancestors in self.rows(self.region('Sidebar')):
            if role != 'AXButton' or expanded(node) is None or not ancestors:
                continue
            title = label(node)
            parent = ancestors[-1]
            # Only actual folder sections have this paired create control.
            create = [child for child in parent.get('AXChildren') or []
                      if child.get('AXRole') == 'AXButton'
                      and child.get('AXDescription') == 'New session in ' + title]
            if len(create) == 1:
                result.append(Item(title, node, parent))
        if not result:
            raise Changed('No verified Claude Code folder sections are exposed.')
        return result

    def folder(self, title):
        items = [item for item in self.folders() if item.title.casefold() == title.casefold()]
        return self.unique(items, 'folder ' + repr(title))

    def folder_sessions(self, title):
        item = self.read(lambda: self.folder(title))
        if expanded(item.node) is False:
            self.log('Expanding Claude folder: ' + item.title)
            item.node.press()
            def expanded_folder():
                current = self.folder(title)
                if expanded(current.node) is not True:
                    raise Changed('Waiting for folder expansion: ' + title)
                return current
            self.wait_read(expanded_folder)
        # Explicitly exposed Show N more controls are safe paging operations.
        # Reacquire both the section and rows after every press.
        for page in range(25):
            item = self.read(lambda: self.folder(title))
            sessions = self.read(lambda: self.sidebar_sessions(self.folder(title).container))
            more = [n for n, role, _ in self.rows(item.container)
                    if role == 'AXButton' and re.fullmatch(r'Show \d+ more in ' + re.escape(item.title), label(n))]
            if not more:
                return sessions
            button = self.unique(more, 'Show more control for ' + repr(title))
            before = len(sessions)
            self.log('Loading more Claude sessions in ' + title)
            button.press()
            def loaded():
                current = self.sidebar_sessions(self.folder(title).container)
                if len(current) <= before:
                    raise Changed('Show more did not expose additional session rows in ' + title)
                return current
            self.wait_read(loaded)
        raise SidebarError('Claude folder paging reached its limit; no partial list returned.')

    def chat_recent_sessions(self):
        def section():
            sidebar = self.region('Sidebar')
            matches = [(n, ancestors[-1]) for n, role, ancestors in self.rows(sidebar)
                       if role == 'AXButton' and label(n) == 'Chats and tasks' and expanded(n) is not None]
            return self.unique(matches, 'Chats and tasks section')
        header, _ = self.read(section)
        if expanded(header) is False:
            header.press()
            def ready():
                head, parent = section()
                if expanded(head) is not True:
                    raise Changed('Waiting for Chats and tasks expansion.')
                return head, parent
            self.wait_read(ready)
        header, parent = self.read(section)
        active, result, ended = False, [], False
        for child in parent.get('AXChildren') or []:
            if child == header:
                active = True
                continue
            if not active:
                continue
            if child.get('AXRole') == 'AXButton' and child.get('AXTitle') == 'View all':
                ended = True
                break
            result.extend(self.sidebar_sessions(child))
        if not ended:
            raise Changed('Could not identify the end of Chats and tasks; no unscoped list returned.')
        return result

    def project_list(self):
        pane = self.region('Primary pane')
        rows = self.rows(pane)
        headings = [n for n, role, _ in rows if role == 'AXHeading' and label(n) == 'Projects' and n.get('AXValue') == 1]
        lists = [n for n, role, _ in rows if role == 'AXList' and n.get('AXDescription') == 'Projects']
        if len(headings) != 1 or len(lists) != 1:
            raise Changed('Claude Projects page is not ready.')
        return [Item(label(n), n) for n, role, _ in self.rows(lists[0]) if role == 'AXLink' and label(n)]

    def open_projects(self):
        try:
            return self.project_list()
        except Changed:
            button = self.read(lambda: self.named_button(self.region('Sidebar'), 'All projects', 'AXDescription'))
            self.log('Opening Claude Projects...')
            button.press()
            return self.wait_read(self.project_list)

    def project_sessions_on_page(self, title):
        pane = self.region('Primary pane')
        rows = self.rows(pane)
        headings = [n for n, role, _ in rows if role == 'AXHeading' and n.get('AXValue') == 1 and label(n) == title]
        rename = [n for n, role, _ in rows if role == 'AXButton' and n.get('AXDescription') == 'Rename ' + title]
        if len(headings) != 1 or len(rename) != 1:
            raise Changed('Waiting for Claude project: ' + title)
        recents = [(n, ancestors[-1]) for n, role, ancestors in rows
                   if role == 'AXHeading' and label(n) == 'Recents' and n.get('AXValue') == 2]
        if not recents:
            empty = any(role == 'AXStaticText' and label(n) ==
                        'Give Claude a task and it’ll pick up your project context automatically.'
                        for n, role, _ in rows)
            if empty:
                return []
            raise Changed('No verified session table or empty state for project: ' + title)
        _, section = self.unique(recents, 'project Recents section')
        tables = [n for n, role, _ in self.rows(section) if role == 'AXTable']
        table = self.unique(tables, 'project session table')
        result = []
        for row in table.get('AXChildren') or []:
            if row.get('AXRole') != 'AXRow':
                continue
            contents = self.rows(row)
            links = [n for n, role, _ in contents if role == 'AXLink']
            menus = [n for n, role, _ in contents if role == 'AXPopUpButton'
                     and (n.get('AXDescription') or '').startswith('More options for ')]
            if not links and not menus:
                continue  # Table header, not an empty/failed session row.
            link = self.unique(links, 'project session link')
            menu = self.unique(menus, 'project session menu')
            title_text = label(link)
            if menu.get('AXDescription') != 'More options for ' + title_text:
                raise Changed('Project session link and menu disagree.')
            result.append(Item(title_text, link))
        return result

    def chat_project_sessions(self, title):
        projects = self.open_projects()
        target = self.unique([p for p in projects if p.title.casefold() == title.casefold()], 'project ' + repr(title))
        self.log('Opening Claude project: ' + target.title)
        target.node.press()
        return self.wait_read(lambda: self.project_sessions_on_page(target.title))

    def debug(self):
        lines = []
        for description in ('Sidebar', 'Primary pane'):
            root = self.region(description)
            lines.append('REGION: ' + description)
            for node, role, ancestors in self.rows(root):
                if role in ('AXButton', 'AXPopUpButton', 'AXLink', 'AXHeading', 'AXList', 'AXTable', 'AXRadioButton'):
                    lines.append(f'depth={len(ancestors)} {role} label={label(node)!r} expanded={expanded(node)!r}')
        return '\n'.join(lines)

    def status(self, mode):
        """Read-only: view, whether the open conversation is responding, and sidebar states.

        While Claude responds, the composer shows a button described exactly "Stop"
        (observed on 2.9939.2). Sidebar rows prefix their titles with a state such as
        "Running " or "Unread response ".
        """
        busy = any(role == 'AXButton' and n.get('AXDescription') == 'Stop'
                   for n, role, _ in self.walk(self.region('Primary pane'), 20000))
        lines = [f'mode: {mode}', 'busy: ' + ('yes' if busy else 'no')]
        for n, role, _ in self.rows(self.region('Sidebar')):
            text = label(n) if role == 'AXButton' else ''
            for prefix, key in (('Running ', 'running'), ('Unread response ', 'unread')):
                if text.startswith(prefix):
                    lines.append(f'{key}: {text[len(prefix):]}')
        return '\n'.join(lines)

    # Conversation: native reading and input (the System Events versions walked the whole
    # window element by element and took seconds; the primary pane walks in ~0.1 s).

    def pane_rows(self):
        return list(self.walk(self.region('Primary pane'), 60000))

    def latest_reply(self):
        """Static text of the last 'Claude responded:' block, as the AppleScript reader did:
        within the heading's container, up to its toolbar or the next 'You said:'."""
        rows = self.pane_rows()
        start = next((i for i in range(len(rows) - 1, -1, -1) if rows[i][1] == 'AXHeading'
                      and label(rows[i][0]).startswith('Claude responded:')), None)
        if start is None:
            raise SidebarError('No Claude response found.')
        heading, container = label(rows[start][0]), (rows[start][2] or (None,))[-1]
        lines, previous = [], None
        for node, role, ancestors in rows[start + 1:]:
            if container is not None and container not in ancestors:
                break  # Left the response block.
            if role == 'AXToolbar' or (role == 'AXHeading' and label(node).startswith('You said:')):
                break
            if role == 'AXStaticText':
                text = node.get('AXValue')
                if isinstance(text, str) and text and text != previous and not text.startswith('Claude responded:'):
                    lines.append(text)
                    previous = text
        return '\n'.join(lines) if lines else heading

    def composer(self):
        areas = [n for n, role, _ in self.pane_rows() if role == 'AXTextArea']
        prompts = [n for n in areas if n.get('AXDescription') == 'Prompt']
        if len(prompts) == 1:
            return prompts[0]
        if not prompts and len(areas) == 1:
            return areas[0]  # Older layouts lack the label; accept only a unique editor.
        raise SidebarError('Cannot identify one Claude prompt text area. Close other editors and retry.')

    @staticmethod
    def draft(editor):
        return (editor.get('AXValue') or '').strip('\n')

    def focus_prompt(self):
        # Focus lands a moment after it is set; retry with fresh references (harmless).
        for _ in range(12):
            try:
                editor = self.composer()
                editor.set_bool('AXFocused', True)
                time.sleep(0.15)
                if editor.get('AXFocused') is True:
                    return editor
            except (Changed, AXError):
                time.sleep(0.15)
        raise SidebarError('Could not focus Claude prompt; nothing entered.')

    def input(self, command, text=''):
        editor = self.read(self.composer)
        if command in ('type', 'send'):
            if self.draft(editor):
                raise SidebarError('Prompt already contains a draft; send or clear it first.')
        elif not self.draft(editor):
            raise SidebarError('Prompt is empty; nothing submitted.')
        self.focus_prompt()
        if command in ('type', 'send'):
            keys('paste', text)
            expected = ' '.join(text.split())
            for _ in range(40):
                try:
                    if ' '.join(self.draft(self.composer()).split()) == expected:
                        break
                except (Changed, AXError):
                    pass
                time.sleep(0.1)
            else:
                raise SidebarError('Could not verify text in Claude prompt; nothing submitted.')
            if command == 'type':
                return 'Typed.'
            self.focus_prompt()
        keys('return')
        for _ in range(40):
            time.sleep(0.1)
            try:
                if not self.draft(self.composer()):
                    return 'Sent.'
            except (Changed, AXError, SidebarError):
                pass
        raise SidebarError('Return pressed, but the prompt did not clear. Check Claude before retrying; '
                           'the message may already be sent.')

    def open_session(self, query):
        """Press a sidebar session row: exact title first, else the first title containing it."""
        items = self.read(lambda: self.sidebar_sessions(self.region('Sidebar')))
        exact = [i for i in items if i.title.casefold() == query.casefold()]
        matches = exact or [i for i in items if query.casefold() in i.title.casefold()]
        if not matches:
            raise LookupError(query)
        matches[0].node.press()
        return 'Opened: ' + matches[0].title

    def run(self, command, project='', recents=False):
        mode = self.read(self.mode)
        if command == 'debug-sidebar':
            return self.debug()
        if command == 'status':
            return self.read(lambda: self.status(mode))
        if command == 'projects':
            items = self.open_projects() if mode == 'chat' else [p for p in self.read(self.folders) if p.title != 'No folder']
        elif project:
            items = self.chat_project_sessions(project) if mode == 'chat' else self.folder_sessions(project)
        elif mode == 'chat':
            self.log('Claude Recents: rows under Chats and tasks (may also belong to projects).')
            items = self.read(self.chat_recent_sessions)
        elif recents:
            self.log('Claude Code Recents: unassigned sessions under No folder.')
            folders = self.read(self.folders)
            items = self.folder_sessions('No folder') if any(p.title == 'No folder' for p in folders) else []
        else:
            items = []
            for folder in self.read(self.folders):
                items.extend(self.folder_sessions(folder.title))
        if self.read(self.mode) != mode:
            raise SidebarError('Claude mode changed during listing; no mixed-mode list returned.')
        self.log(f'Claude {mode}: read {len(items)} ' + ('project rows.' if command == 'projects' else 'session rows.'))
        names = [item.title for item in items]
        if project:
            return '[' + project + ']\n' + '\n'.join('  ' + name for name in names)
        return '\n'.join(names)


KEYS = '''
tell application id "com.anthropic.claudefordesktop" to activate
delay 0.1
tell application "System Events"
    set frontBundle to bundle identifier of first application process whose frontmost is true
end tell
if frontBundle is not "com.anthropic.claudefordesktop" then error "Claude is not frontmost; no keys were sent."
if (system attribute "CTL_KEY") is "paste" then
    -- Clipboard paste handles long, multiline and Unicode text; restore the clipboard afterwards.
    set oldClipboard to missing value
    try
        set oldClipboard to the clipboard as record
    end try
    try
        set the clipboard to (system attribute "CTL_PASTE")
        tell application "System Events" to keystroke "v" using command down
        delay 0.4
    on error errText number errNum
        if oldClipboard is not missing value then set the clipboard to oldClipboard
        error errText number errNum
    end try
    if oldClipboard is not missing value then set the clipboard to oldClipboard
else
    tell application "System Events" to key code 36
end if
'''


def keys(action, text=''):
    env = dict(os.environ, CTL_KEY=action, CTL_PASTE=text)
    result = subprocess.run(['osascript', '-e', KEYS], env=env, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SidebarError(result.stderr.strip() or 'Keyboard input failed.')


ACTIVATE = '''tell application "Claude" to activate
tell application "System Events" to tell process "Claude"
    set frontmost to true
    return unix id
end tell'''


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    conversation = ('read', 'type', 'send', 'enter', 'return', 'session')
    if not args or args[0] not in ('projects', 'sessions', 'debug-sidebar', 'status') + conversation:
        print('Use agent_ctl.py --app claude projects or sessions [--project NAME | --recents].', file=sys.stderr)
        return 2
    command, project, recents = args[0], '', False
    if command in conversation:
        pass
    elif command == 'sessions' and args[1:2] == ['--project'] and len(args) == 3 and args[2].strip():
        project = args[2]
    elif command == 'sessions' and args[1:] == ['--recents']:
        recents = True
    elif len(args) != 1:
        print('Use sessions, sessions --project "name", or sessions --recents; projects takes no arguments.', file=sys.stderr)
        return 2
    try:
        api = NativeAX()
        if not api.ax.AXIsProcessTrusted():
            raise SidebarError('Accessibility access is not enabled for this terminal.')
        result = subprocess.run(['osascript', '-e', ACTIVATE], capture_output=True, text=True, timeout=10, check=True)
        app = api.application(int(result.stdout.strip()))
        time.sleep(0.2)
        reader = ClaudeSidebar(app, seconds=65)
        text = ' '.join(args[1:])
        if command == 'read':
            print(reader.read(reader.latest_reply))
        elif command == 'session':
            try:
                print(reader.open_session(text))
            except LookupError:
                return 3  # Not in the sidebar: the caller falls back to a whole-window search.
        elif command in ('type', 'send', 'enter', 'return'):
            print(reader.input('enter' if command == 'return' else command, text))
        else:
            print(reader.run(command, project, recents))
        return 0
    except (SidebarError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Claude listing cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
