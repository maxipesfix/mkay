"""Cursor backend: the Agents window and IDE windows, entirely through native AX.

Cursor's two views are separate windows. The view is whichever window is main:
"Cursor Agents" is the Agents view; any other standard window is the IDE view.
Cursor rejects AXEnhancedUserInterface and exposes its tree via AXManualAccessibility.

Agents view: projects are the sidebar's Repositories groups; sessions are the
agent rows inside them. Cursor's own "Projects" sidebar section is not listed.
IDE view: projects are the open workspace windows; sessions are open agent chat tabs.
Chat messages and composers are identified by Cursor's DOM classes, which the
Chromium accessibility tree exposes as AXDOMClassList.
"""
import os
import re
import subprocess
import sys
import time

from ax_native import AXError, Changed, NativeAX, SidebarError, Walker, expanded, label

BUNDLE = 'com.todesktop.230313mzl4w4u92'
AGENTS_TITLE = 'Cursor Agents'
DISPLAY = {'agents': 'Agents', 'ide': 'IDE'}
ALIASES = {'agents': 'agents', 'agent': 'agents', 'ide': 'ide', 'editor': 'ide'}
# Trailing relative age in sidebar rows, e.g. "Code submission review 2d".
AGE = re.compile(r'now|\d+(s|m|h|d|w|mo|y)')
# Codicon/icon-font glyphs live in the Unicode private use area.
GLYPHS = re.compile('[\\ue000-\\uf8ff]')
COMPOSERS = ('aislash-editor-input', 'ui-prompt-input-editor__input')
# A pending multiple-choice question: Agents-window tray / IDE composer toolbar.
QUESTION_TRAYS = ('glass-questionnaire-tray', 'composer-questionnaire-toolbar')
QUESTION_OPTIONS = ('ui-tray-option', 'composer-questionnaire-toolbar-option')
QUESTION_CHROME = {'Question', 'Questions', 'Skip', 'Esc', 'Continue', 'of'}
PAGING = re.compile(r'(show|view|load)\b.*\b(more|all)\b', re.IGNORECASE)
INLINE_ROLES = {'AXButton', 'AXLink', 'AXStaticText'}
# Idle, the Agents composer's submit button reads "Send message"; while the agent works,
# a stop control takes its place. Only controls inside a composer count: the IDE's
# debugger also has "Stop" buttons.
STOP_LABEL = re.compile(r'(stop|cancel)( generating| generation| response| agent)?(\s*\(.*\)|\s+[^\w\s].*)?',
                        re.IGNORECASE)  # Optional shortcut hint, e.g. "Stop (⌘⌫)"; not "Stop voice input".
COMPOSER_AREAS = ('composer-bar', 'ui-prompt-input')


def clean(text):
    return ' '.join(GLYPHS.sub('', text or '').split())


def classes(node):
    value = node.get('AXDOMClassList')
    return value if isinstance(value, list) else []


class Cursor(Walker):
    # IDE windows expose editors, terminals and panels; the file tree is never needed.
    STACK_BUDGET = 20000
    PRUNE = frozenset({'AXOutline'})
    LIMIT = 20000

    # Windows and views

    def windows(self):
        return [w for w in self.app.get('AXWindows') or []
                if w.get('AXSubrole') == 'AXStandardWindow']

    def main_window(self):
        window = self.app.get('AXMainWindow')
        if window is None:
            raise SidebarError('Cursor has no main window. Open the Agents window or a workspace.')
        return window

    def agents_window(self):
        return self.unique([w for w in self.windows() if w.get('AXTitle') == AGENTS_TITLE],
                           'Cursor Agents window')

    def ide_windows(self):
        return [w for w in self.windows() if w.get('AXTitle') != AGENTS_TITLE]

    def mode(self):
        return 'agents' if self.main_window().get('AXTitle') == AGENTS_TITLE else 'ide'

    def switch(self, wanted):
        current = self.read(self.mode)
        if current == wanted:
            return 'Already in ' + DISPLAY[wanted]
        def find_button():
            # A read can meet an element that vanished mid-scan (-25202); self.read retries
            # the scan with fresh references. The press itself is never retried.
            window = self.main_window()
            if wanted == 'ide':
                buttons = [n for n, role, _ in self.walk(window, self.LIMIT)
                           if role == 'AXButton' and n.get('AXDescription') == 'IDE']
            else:
                buttons = [n for n, role, _ in self.walk(window, self.LIMIT)
                           if role == 'AXButton' and 'open-agents-window-button' in classes(n)]
            return self.unique(buttons, DISPLAY[wanted] + ' switch button')
        self.read(find_button).press()
        for _ in range(15):
            time.sleep(0.2)
            try:
                if self.mode() == wanted:
                    return 'Switched to ' + DISPLAY[wanted]
            except (Changed, AXError):
                pass
        raise SidebarError('Pressed the ' + DISPLAY[wanted] + ' button, but the view change was not confirmed. '
                           'Run mode to check; no click was retried.')

    # Agents sidebar

    def row_title(self, node):
        texts = [clean(label(n)) for n, role, _ in self.walk(node, 200) if role == 'AXStaticText']
        texts = [t for t in texts if t]
        if len(texts) > 1 and AGE.fullmatch(texts[-1]):
            texts = texts[:-1]
        return ' '.join(texts) if texts else clean(label(node))

    def sidebar(self):
        window = self.agents_window()
        navs = [n for n, role, _ in self.walk(window, self.LIMIT)
                if role == 'AXGroup' and n.get('AXSubrole') == 'AXLandmarkNavigation']
        if not navs:
            raise SidebarError('The Agents sidebar is hidden. Open it in Cursor and rerun.')
        return self.unique(navs, 'Agents sidebar')

    def repositories_header(self):
        headers = [(n, anc) for n, role, anc in self.walk(self.sidebar(), self.LIMIT)
                   if role == 'AXButton' and expanded(n) is not None
                   and label(n).split(' ', 1)[0] == 'Repositories']
        return self.unique(headers, 'Repositories section')

    def repositories(self):
        header, ancestors = self.repositories_header()
        if expanded(header) is False:
            self.log('Expanding Cursor Repositories...')
            header.press()
            time.sleep(0.4)
            header, ancestors = self.wait_read(self.repositories_header)
            if expanded(header) is not True:
                raise SidebarError('Could not confirm expansion of Repositories.')
        siblings = ancestors[-1].get('AXChildren') or []
        if header not in siblings or siblings.index(header) + 1 >= len(siblings):
            raise Changed('Repositories list is not exposed after its header.')
        container = siblings[siblings.index(header) + 1]
        repos = []
        for group in container.get('AXChildren') or []:
            found = [(n, anc) for n, role, anc in self.walk(group, 400)
                     if role == 'AXButton' and expanded(n) is not None]
            if not found:
                continue
            node, anc = found[0]
            repos.append((self.row_title(node), node, anc[-1] if anc else group))
        return repos

    def repository(self, title):
        matches = [r for r in self.repositories() if r[0].casefold() == title.casefold()]
        return self.unique(matches, 'repository ' + repr(title))

    def repository_sessions(self, title):
        name, node, _ = self.read(lambda: self.repository(title))
        if expanded(node) is False:
            self.log('Expanding Cursor repository: ' + name)
            node.press()

            def ready():
                current = self.repository(title)
                if expanded(current[1]) is not True:
                    raise Changed('Waiting for repository expansion: ' + title)
                return current
            self.wait_read(ready)
            time.sleep(0.3)
        _, node, box = self.read(lambda: self.repository(title))
        rows, covered = [], set()
        for n, role, anc in self.walk(box, 4000):
            if role != 'AXButton' or n == node or not any(a.get('AXRole') == 'AXList' for a in anc):
                continue
            if any(a in covered for a in anc):
                continue  # A control nested inside a session row.
            covered.add(n)
            title_text = self.row_title(n)
            if PAGING.search(title_text):
                self.log(f'Note: {name} has a "{title_text}" control; more sessions may exist than listed.')
                continue
            rows.append((title_text, n))
        return name, rows

    def agent_sessions(self):
        result = []
        for name, _, _ in self.read(self.repositories):
            result.extend(self.repository_sessions(name)[1])
        return result

    # IDE windows

    @staticmethod
    def workspace(window):
        title = window.get('AXTitle') or ''
        return title.rsplit(' — ', 1)[-1]

    def chat_tabs(self, window):
        tabs = []
        for n, role, _ in self.walk(window, self.LIMIT):
            if role != 'AXRadioButton' or n.get('AXSubrole') != 'AXTabButton':
                continue
            labels = [c for c in n.get('AXChildren') or [] if 'composer-tab-label' in classes(c)]
            if labels:
                tabs.append((clean(labels[0].get('AXDescription') or self.row_title(labels[0])), n))
        return tabs

    def raise_window(self, window):
        # AXRaise orders the window front; AXMain makes Cursor treat it as main.
        window.perform('AXRaise')
        window.set_bool('AXMain', True)
        name = window.get('AXTitle')
        for _ in range(15):
            time.sleep(0.1)
            if self.main_window().get('AXTitle') == name:
                return
        raise SidebarError('Raised window ' + repr(name) + ', but it did not become the main window.')

    def open_project(self, name):
        window = self.read(lambda: self.ide_window(name))
        if self.main_window() == window:
            return 'Already in workspace ' + self.workspace(window)
        self.raise_window(window)
        return 'Switched to workspace ' + self.workspace(window) + ' (IDE)'

    def ide_window(self, project):
        matches = [w for w in self.ide_windows() if self.workspace(w).casefold() == project.casefold()]
        return self.unique(matches, 'IDE window for workspace ' + repr(project))

    # Conversation and composer

    def composer(self, window):
        editors = [n for n, role, _ in self.walk(window, self.LIMIT)
                   if role == 'AXTextArea' and any(c in classes(n) for c in COMPOSERS)]
        if not editors:
            raise SidebarError('No Cursor agent composer is visible in the main window. Open an agent chat.')
        return self.unique(editors, 'agent composer (close other visible agent chats)')

    @staticmethod
    def is_empty(editor):
        # An empty editor reports its placeholder as AXValue; its paragraph is
        # then marked is-editor-empty.
        value = editor.get('AXValue') or ''
        if not value.strip():
            return True
        return any('is-editor-empty' in classes(child) for child in editor.get('AXChildren') or [])

    @staticmethod
    def draft(editor):
        return (editor.get('AXValue') or '').rstrip('\n')

    def transcript_rows(self, window):
        containers = [n for n, role, _ in self.walk(window, self.LIMIT)
                      if role == 'AXGroup' and 'composer-messages-container' in classes(n)]
        if not containers:
            raise SidebarError('No Cursor agent conversation is visible in the main window.')
        container = self.unique(containers, 'agent conversation (close other visible agent chats)')
        rows = []
        for n, role, anc in self.walk(container, self.LIMIT):
            node_classes = classes(n)
            if 'virtualized-composer-messages-row' in node_classes:
                rows.append([n, False])
            elif 'composer-human-message' in node_classes and rows and rows[-1][0] in anc:
                rows[-1][1] = True
        return rows

    def text(self, root):
        """Join text fragments into lines, one line per paragraph or list item."""
        lines, current, block = [], [], None
        in_control = False

        def flush():
            line = clean(''.join(current))
            if line:
                lines.append(line)
            current.clear()

        def add(fragment, control):
            nonlocal in_control
            # Prose fragments carry their own spacing; control labels such as
            # "Explored" + "1 search", inline file chips and tool-call rows
            # ("Check remotes" + "git, echo") do not. A leading "." followed by a letter is a
            # file name (".gitignore"), not sentence punctuation.
            if not (current and fragment) or current[-1][-1:].isspace() or fragment[:1].isspace():
                pass
            elif ((control or in_control) and (fragment[:1] not in '.,;:!?)' or fragment[1:2].isalnum())
                  or current[-1][-1:].isalnum() and fragment[:1].isalnum()):
                current.append(' ')
            current.append(fragment)
            in_control = control
        for n, role, anc in self.walk(root, self.LIMIT):
            if role == 'AXListMarker':
                flush()
                current.append(label(n))
                block = anc[-1] if anc else None
                continue
            childless_control = role in ('AXButton', 'AXLink') and not n.get('AXChildren')
            if role != 'AXStaticText' and not childless_control:
                continue
            owner = next((a for a in reversed(anc) if a.get('AXRole') not in INLINE_ROLES
                          and a.get('AXSubrole') != 'AXCodeStyleGroup'), None)
            if owner != block:
                flush()
                block = owner
            add(label(n), childless_control or any(a.get('AXRole') in ('AXButton', 'AXLink') for a in anc))
        flush()
        return '\n'.join(lines)

    def question_state(self, window):
        """The pending multiple-choice question in a window, or None.

        Returns the prompt text, options by letter (text, button, freeform?), and
        the tray element. Several questions can be pending; Cursor shows one at a time.
        """
        trays = [n for n, role, _ in self.walk(window, self.LIMIT)
                 if role == 'AXGroup' and any(c in classes(n) for c in QUESTION_TRAYS)]
        if not trays:
            return None
        tray = self.unique(trays, 'pending Cursor question (close other visible agent chats)')
        prompt, options = [], {}
        for n, role, anc in self.walk(tray, 2000):
            in_option = any(any(c in classes(a) for c in QUESTION_OPTIONS) for a in anc)
            if role == 'AXButton' and any(c in classes(n) for c in QUESTION_OPTIONS):
                match = re.fullmatch(r'([A-Z]) (.+)', clean(label(n)), re.DOTALL)
                letter, text = (match[1], match[2]) if match else (chr(ord('A') + len(options)), clean(label(n)))
                options[letter] = (text, n, any('freeform' in c for c in classes(n)))
            elif role == 'AXStaticText' and not in_option:
                text = clean(label(n))
                if text and text not in QUESTION_CHROME and not re.fullmatch(r'[\d\s.⏎]*', text):
                    prompt.append(text)
        return {'prompt': ' '.join(prompt), 'options': options, 'tray': tray}

    @staticmethod
    def question_lines(state):
        return (['Pending question: ' + state['prompt']]
                + [f'  {letter}. {text}' for letter, (text, _, _) in state['options'].items()])

    def question(self, window):
        """Lines for a pending multiple-choice question, or None when there is none."""
        state = self.question_state(window)
        return self.question_lines(state) if state else None

    @staticmethod
    def question_key(state):
        return (state['prompt'], tuple(text for text, _, _ in state['options'].values())) if state else None

    def continue_control(self, tray):
        """The tray's Continue control: a button or a clickable group, by its visible text."""
        found = []
        for n, role, anc in self.walk(tray, 2000):
            if role == 'AXStaticText' and clean(label(n)) == 'Continue':
                owner = next((a for a in reversed(anc) if a.get('AXRole') == 'AXButton'
                              or 'cursor-pointer' in classes(a)), None)
                if owner is not None:
                    found.append(owner)
            elif role == 'AXButton' and clean(label(n)).split(' ')[0] == 'Continue':
                found.append(n)
        found = list(dict.fromkeys(found))
        return found[0] if len(found) == 1 else None

    def answer(self, letter, text):
        window = self.main_window()
        state = self.read(lambda: self.question_state(window))
        if state is None:
            raise SidebarError('No pending Cursor question in the main window.')
        letter = letter.upper()
        if letter not in state['options']:
            raise SidebarError(f'Option {letter} not offered; choose one of ' + ', '.join(state['options']) + '.')
        _, button, freeform = state['options'][letter]
        if freeform and not text.strip():
            raise SidebarError(f'Option {letter} is a free-text answer: pass the text, e.g. answer {letter} "..."')
        if not freeform and text.strip():
            raise SidebarError(f'Option {letter} takes no text; only a free-text option does.')
        before = self.question_key(state)
        self.log(f'Choosing option {letter}: {state["options"][letter][0]}')
        button.press()
        time.sleep(0.4)
        if freeform:
            fields = [n for n, role, _ in self.walk(self.question_state(window)['tray'], 2000)
                      if role == 'AXTextArea']
            field = self.unique(fields, 'free-text answer field')
            self.focus(field)
            self.keys('paste', text)
            expected = ' '.join(text.split())
            for _ in range(50):
                current = self.question_state(window)
                values = [' '.join((n.get('AXValue') or '').split()) for n, role, _
                          in self.walk(current['tray'], 2000) if role == 'AXTextArea'] if current else []
                if expected in values:
                    break
                time.sleep(0.1)
            else:
                raise SidebarError('Could not verify the free-text answer; nothing submitted. Check Cursor.')
        pressed_continue = False
        for attempt in range(50):
            try:
                current = self.question_state(window)
            except (Changed, AXError):
                time.sleep(0.1)
                continue
            if current is None:
                return 'done'
            if self.question_key(current) != before:
                return '\n'.join(self.question_lines(current))
            if not pressed_continue and (freeform or attempt >= 5):
                # One click selects in some layouts; Continue then submits. Pressed once, never retried.
                control = self.continue_control(current['tray'])
                if control is None:
                    raise SidebarError(f'Chose option {letter}, but the question is still shown and no single '
                                       'Continue control was found. Check Cursor; nothing was retried.')
                self.log('Pressing Continue...')
                control.press()
                pressed_continue = True
            time.sleep(0.1)
        raise SidebarError(f'Chose option {letter}, but the question did not change. '
                           'Check Cursor before retrying; the answer may already be recorded.')

    def latest_reply(self):
        window = self.main_window()
        rows = self.transcript_rows(window)
        humans = [i for i, (_, human) in enumerate(rows) if human]
        start = humans[-1] + 1 if humans else 0
        parts = [self.text(node) for node, _ in rows[start:]]
        question = self.question(window)
        if question:
            parts.append('\n'.join(question))
        output = '\n'.join(p for p in parts if p)
        if not output:
            raise SidebarError('No agent reply text is exposed after the latest user message.')
        return output

    def busy(self, window):
        """True while the main window's agent chat is working; None without a visible composer."""
        composer_seen = False
        for n, role, anc in self.walk(window, self.LIMIT):
            node_classes = classes(n)
            if role == 'AXTextArea' and any(c in node_classes for c in COMPOSERS):
                composer_seen = True
            if role != 'AXButton':
                continue
            in_composer = ('ui-prompt-input-submit-button' in node_classes or any(
                any(c.startswith(area) for c in classes(a) for area in COMPOSER_AREAS) for a in anc))
            if in_composer and STOP_LABEL.fullmatch(clean(label(n))):
                return True
        return False if composer_seen else None

    def status(self, mode):
        window = self.main_window()
        busy = self.busy(window)
        question = self.question_state(window) is not None
        return '\n'.join([f'mode: {mode}', 'busy: ' + {True: 'yes', False: 'no', None: 'unknown'}[busy],
                          'question: ' + ('yes' if question else 'no')])

    # Commands

    def run(self, command, argument='', project=''):
        mode = self.read(self.mode)
        if command == 'mode':
            if not argument:
                return mode
            return self.switch(argument)
        if command == 'debug-mode':
            return self.debug_mode()
        if command == 'status':
            return self.read(lambda: self.status(mode))
        if command == 'debug-sidebar':
            return self.debug_sidebar(mode)
        if command == 'projects':
            if mode == 'agents':
                names = [name for name, _, _ in self.read(self.repositories)]
            else:
                names = list(dict.fromkeys(self.workspace(w) for w in self.ide_windows()))
            self.log(f'Cursor {mode}: read {len(names)} project rows.')
            return '\n'.join(names)
        if command == 'sessions':
            if mode == 'agents':
                items = self.repository_sessions(project)[1] if project else self.agent_sessions()
            elif project:
                items = self.chat_tabs(self.ide_window(project))
            else:
                items = [tab for w in self.ide_windows() for tab in self.chat_tabs(w)]
            if self.read(self.mode) != mode:
                raise SidebarError('Cursor view changed during listing; no mixed-view list returned.')
            self.log(f'Cursor {mode}: read {len(items)} session rows.')
            names = [title for title, _ in items]
            if project:
                return '[' + project + ']\n' + '\n'.join('  ' + name for name in names)
            return '\n'.join(names)
        if command == 'session':
            return self.open_session(mode, argument)
        if command == 'project':
            return self.open_project(argument)
        if command == 'read':
            return self.read(self.latest_reply)
        if command == 'answer':
            letter, _, text = argument.partition(' ')
            return self.answer(letter, text)
        return self.input(command, argument)

    def open_session(self, mode, query):
        if mode == 'agents':
            candidates = [(title, node, None) for title, node in self.agent_sessions()]
        else:
            candidates = [(title, node, w) for w in self.ide_windows() for title, node in self.chat_tabs(w)]
        exact = [c for c in candidates if c[0].casefold() == query.casefold()]
        matches = exact or [c for c in candidates if query.casefold() in c[0].casefold()]
        if len(matches) != 1:
            raise SidebarError(f'Session not found or title is ambiguous ({len(matches)} matches): ' + query)
        title, node, window = matches[0]
        if window is not None and self.main_window() != window:
            self.raise_window(window)
        node.press()
        return 'Opened: ' + title

    def keys(self, action, text=''):
        env = os.environ.copy()
        env.update(CTL_KEY=action, CTL_TEXT=text)
        try:
            result = subprocess.run(['osascript', '-e', KEYS], env=env, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise SidebarError('Keyboard input timed out. Check Cursor before retrying.')
        if result.returncode:
            raise SidebarError(result.stderr.strip() or 'Keyboard input failed.')

    def focus(self, editor):
        editor.set_bool('AXFocused', True)
        time.sleep(0.1)
        if editor.get('AXFocused') is not True:
            raise SidebarError('Could not focus the Cursor composer; nothing entered.')

    def fresh_draft(self):
        # Cursor can replace the editor element while it renders pasted text,
        # leaving an earlier reference stale. Reacquire it for every check.
        try:
            return self.draft(self.composer(self.main_window()))
        except (Changed, AXError):
            return None

    def input(self, command, text):
        window = self.main_window()
        editor = self.read(lambda: self.composer(window))
        if command != 'type' and self.question(window):
            raise SidebarError('Cursor is waiting for an answer to its question (run read to see it). '
                               'The composer only adds optional details to that answer, so nothing was '
                               'submitted. Answer the question in Cursor first.')
        if command in ('type', 'send'):
            if not self.is_empty(editor):
                raise SidebarError('Prompt already contains a draft; send or clear it first.')
            self.focus(editor)
            self.keys('paste', text)
            expected = ' '.join(text.split())
            for _ in range(50):
                draft = self.fresh_draft()
                if draft is not None and ' '.join(draft.split()) == expected:
                    break
                time.sleep(0.1)
            else:
                raise SidebarError('Could not verify pasted text in the Cursor composer; nothing submitted. '
                                   'Check the composer: the text may have been pasted.')
            if command == 'type':
                return 'Typed and verified.'
            editor = self.read(lambda: self.composer(self.main_window()))
            self.focus(editor)
        else:
            if self.is_empty(editor):
                raise SidebarError('Prompt is empty; nothing submitted.')
            self.focus(editor)
        self.keys('return')
        for _ in range(30):
            time.sleep(0.1)
            try:
                if self.is_empty(self.composer(self.main_window())):
                    return 'Sent; prompt cleared.'
            except (Changed, AXError):
                pass
        raise SidebarError('Return was pressed, but the prompt did not clear. '
                           'Check Cursor before retrying; the message may already be sent.')

    # Diagnostics

    def debug_mode(self):
        lines = ['Main window: ' + repr(self.main_window().get('AXTitle')), 'Current view: ' + self.mode()]
        for w in self.windows():
            lines.append(f'Window: {w.get("AXTitle")!r} main={w.get("AXMain")!r}')
            for n, role, _ in self.walk(w, self.LIMIT):
                if role == 'AXButton' and (n.get('AXDescription') == 'IDE'
                                           or 'open-agents-window-button' in classes(n)):
                    lines.append(f'  switch control: {clean(label(n))!r}')
        lines.append('Mode diagnostic complete. No controls pressed.')
        return '\n'.join(lines)

    def debug_sidebar(self, mode):
        lines = []
        if mode == 'agents':
            for n, role, anc in self.walk(self.sidebar(), self.LIMIT):
                if role in ('AXButton', 'AXPopUpButton', 'AXList', 'AXCheckBox'):
                    lines.append(f'depth={len(anc)} {role} label={clean(label(n))!r} expanded={expanded(n)!r}')
        else:
            for w in self.ide_windows():
                lines.append(f'IDE window: {w.get("AXTitle")!r} workspace={self.workspace(w)!r}')
                for title, _ in self.chat_tabs(w):
                    lines.append(f'  chat tab: {title!r}')
        lines.append('Sidebar diagnostic complete. No controls pressed.')
        return '\n'.join(lines)


KEYS = f'''
tell application id "{BUNDLE}" to activate
delay 0.2
tell application "System Events"
    set frontBundle to bundle identifier of first application process whose frontmost is true
end tell
if frontBundle is not "{BUNDLE}" then error "Cursor is not frontmost; no keys were sent."
if (system attribute "CTL_KEY") is "paste" then
    -- Clipboard paste handles long, multiline and Unicode text; restore the clipboard afterwards.
    set oldClipboard to missing value
    try
        set oldClipboard to the clipboard as record
    end try
    try
        set the clipboard to (system attribute "CTL_TEXT")
        tell application "System Events" to keystroke "v" using command down
        delay 0.5
    on error errText number errNum
        if oldClipboard is not missing value then set the clipboard to oldClipboard
        error errText number errNum
    end try
    if oldClipboard is not missing value then set the clipboard to oldClipboard
else
    tell application "System Events" to key code 36
end if
'''

ACTIVATE = f'''
tell application id "{BUNDLE}" to activate
tell application "System Events"
    set p to first application process whose bundle identifier is "{BUNDLE}"
    set frontmost of p to true
    return unix id of p
end tell
'''

COMMANDS = ('mode', 'status', 'debug-mode', 'debug-sidebar', 'projects', 'project', 'sessions', 'session', 'answer',
            'read', 'type', 'send', 'enter', 'return')


def usage():
    print('''Usage: ./agent_ctl.py --app cursor COMMAND [ARGS]
  mode [agents|ide]            Print or switch the view (Agents window or IDE window)
  status                       View, whether the main window's agent is working, pending question
  projects                     Agents: sidebar repositories; IDE: open workspace windows
  sessions [--project NAME]    Agents: agent rows (expands repositories); IDE: open agent chat tabs
  project NAME                 Bring an IDE workspace window forward (switches to the IDE view)
  session "title"              Open an agent session (exact title, else unique substring);
                               in the IDE view this also brings its window forward
  read                         Print the latest agent reply in the main window
  type "text" | send "text"    Paste and verify text in the empty composer; send also presses Return
  enter                        Submit the existing draft (alias: return)
  answer LETTER ["text"]       Answer the pending multiple-choice question (text only for a free-text
                               option such as "Other"). Prints "done", or the next pending question.
  debug-mode, debug-sidebar    Read-only diagnostics
Cursor has no Recents list; sessions --recents is not supported.''')


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    if not args or args[0] in ('-h', '--help', 'help'):
        usage()
        return 0 if args else 2
    command, rest = args[0].lower(), args[1:]
    if command not in COMMANDS:
        print(f'Unknown command: {command}', file=sys.stderr)
        return 2
    project, argument = '', ' '.join(rest)
    if command == 'sessions':
        if rest[:1] == ['--recents']:
            print('Cursor has no Recents list; use sessions or sessions --project NAME.', file=sys.stderr)
            return 2
        if rest[:1] == ['--project']:
            project = ' '.join(rest[1:]).strip()
            if not project:
                print('sessions --project requires a project name.', file=sys.stderr)
                return 2
        elif rest:
            print('Use sessions or sessions --project NAME.', file=sys.stderr)
            return 2
        argument = ''
    elif command == 'mode' and argument:
        if argument.lower() not in ALIASES:
            print('Cursor mode must be agents or ide.', file=sys.stderr)
            return 2
        argument = ALIASES[argument.lower()]
    elif command == 'answer' and not re.fullmatch(r'[A-Za-z]', argument.split(' ', 1)[0] if argument else ''):
        print('Usage: answer LETTER ["text"], e.g. answer B', file=sys.stderr)
        return 2
    elif command in ('session', 'type', 'send', 'project') and not argument.strip():
        print(f'{command} requires non-empty text.', file=sys.stderr)
        return 2
    elif command not in ('mode', 'session', 'type', 'send', 'answer', 'project') and argument:
        print(f'{command} takes no arguments.', file=sys.stderr)
        return 2
    try:
        api = NativeAX()
        if not api.trusted():
            raise SidebarError('Accessibility access is not enabled for this terminal. Enable it in '
                               'System Settings > Privacy & Security > Accessibility, then restart the terminal.')
        result = subprocess.run(['osascript', '-e', ACTIVATE], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise SidebarError('Could not activate Cursor: ' + result.stderr.strip())
        app = api.application(int(result.stdout.strip()), flag='AXManualAccessibility')
        time.sleep(0.3)
        print(Cursor(app, seconds=60).run(command, argument, project))
        return 0
    except (SidebarError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Cursor command cancelled. Check the app before retrying.', file=sys.stderr)
        return 130
