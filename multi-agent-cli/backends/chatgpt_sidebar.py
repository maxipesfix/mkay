"""ChatGPT/Codex sidebar and mode backend; no screen coordinates or System Events tree paths.

Uses retained native AX references from ax_native. Only activation/PID lookup uses AppleScript.
"""
from collections import Counter
from dataclasses import dataclass
import os
import re
import subprocess
import sys
import time

from ax_native import AXError, Changed, LEAVES, NativeAX, SidebarError, Walker, expanded, label


ANCHORS = {'Project sidebar options', 'Chat sidebar options'}
CONTROLS = {'AXButton', 'AXLink', 'AXRow', 'AXPopUpButton'}
CATEGORIES = {'Projects', 'Recents', 'Chats', 'Tasks', 'Pinned', 'Favorites', 'Scheduled', 'Archived'}
IGNORED = CATEGORIES | {'', 'New chat', 'New task', 'New', 'Search', 'Search chats',
    'Library', 'Settings', 'ChatGPT', 'Codex', 'Hide sidebar', 'Close sidebar',
    'Show sidebar', 'Upgrade', 'Update', 'Explore GPTs', 'Quick chat', 'Back', 'Forward',
    'Pull requests', 'Plugins', 'Add new project', 'Start new voice chat', 'View all'}
# These buttons act on a session; they do not open a session. Exact action labels
# only: titles such as "Implement Pin chat" must remain valid session names.
ROW_ACTION_BUTTONS = {'pin chat', 'unpin chat', 'archive chat', 'unarchive chat'}
# Paging controls: pressed to reveal more rows, never reported as sessions.
MORE = re.compile(r'show( \d+)? more', re.IGNORECASE)
# A project's own new-chat button sits among its rows; it is not a session.
PROJECT_NEW_CHAT = re.compile(r'(start )?new chat in .+', re.IGNORECASE)


@dataclass
class Entry:
    kind: str
    title: str
    node: object
    expanded: object = None


class Sidebar(Walker):
    def main_window(self):
        windows = [win for win in (self.app.get('AXWindows') or [])
                   if win.get('AXTitle') == 'ChatGPT']
        if len(windows) != 1:
            raise SidebarError('Cannot identify one ChatGPT main window.')
        return windows[0]

    def locate(self):
        paths = {}
        for node, role, ancestors in self.walk(self.main_window(), 1200):
            if role not in CONTROLS:
                continue
            title = label(node)
            if title == 'Show sidebar':
                raise SidebarError('The sidebar is hidden. Open it in the app and rerun.')
            if title in ANCHORS:
                paths[title] = ancestors
            if len(paths) == 2:
                shared = None
                for left, right in zip(*paths.values()):
                    if left != right:
                        break
                    shared = left
                if shared is None or shared.get('AXRole') in ('AXWindow', 'AXWebArea'):
                    raise SidebarError('Cannot identify a distinct sidebar container.')
                return shared
        raise Changed('Projects and chat sidebar controls are not fully exposed.')

    def snapshot(self):
        root = self.locate()
        entries, anchors = [], set()
        covered_controls = set()
        for node, role, ancestors in self.walk(root, 2400):
            if role == 'AXTextArea':
                raise SidebarError('Sidebar scope includes a composer; refusing an unscoped list.')
            if role not in CONTROLS:
                continue
            title = label(node)
            if title in ('Rate response', 'Fork chat from here'):
                raise SidebarError('Sidebar scope includes message actions; refusing an unscoped list.')
            if title in ANCHORS:
                anchors.add(title)
                continue
            # A session row may contain its own action buttons or another AX
            # representation of the title. Record the row once, not its controls.
            if any(parent in covered_controls for parent in ancestors):
                continue
            if role == 'AXButton' and title.casefold() in ROW_ACTION_BUTTONS:
                covered_controls.add(node)
                continue
            if role == 'AXPopUpButton':
                continue
            state = expanded(node)
            if state is not None or title in CATEGORIES:
                entries.append(Entry('header', title, node, state))
            elif MORE.fullmatch(title):
                entries.append(Entry('more', title, node))
                covered_controls.add(node)
            elif PROJECT_NEW_CHAT.fullmatch(title):
                covered_controls.add(node)
            elif title not in IGNORED and not title.startswith('More options'):
                entries.append(Entry('session', title, node))
                covered_controls.add(node)
        if anchors != ANCHORS or not root.get('AXRole'):
            raise Changed('Sidebar changed while its rows were being read.')
        headers = [e.title for e in entries if e.kind == 'header']
        self.log(f'Native sidebar: read {sum(e.kind == "session" for e in entries)} session rows across all sections; '
                 f'{len(headers)} section headers.')
        return entries

    def debug(self):
        """Read a bounded sidebar diagnostic without pressing any controls."""
        root = self.locate()
        rows = list(self.walk(root, 2400))
        # Validate scope before collecting or printing any text fragments.
        for node, role, _ in rows:
            if role == 'AXTextArea' or (role in CONTROLS and label(node) in
                                      ('Rate response', 'Fork chat from here')):
                raise SidebarError('Sidebar diagnostic scope includes conversation controls.')
        counts = Counter(role for _, role, _ in rows)
        lines = ['Native sidebar roles: ' + ', '.join(f'{role}={n}' for role, n in sorted(counts.items()))]
        identifiers = {node: index for index, (node, _, _) in enumerate(rows, 1)}
        records = []
        for index, (node, role, ancestors) in enumerate(rows, 1):
            title = label(node)
            advertised = node.supports('AXExpanded')
            raw = node.get('AXExpanded') if role in CONTROLS or advertised else None
            if not title and role not in CONTROLS and not advertised:
                continue
            parent = identifiers.get(ancestors[-1], 0) if ancestors else 0
            children = (node.get('AXChildren') or []) if role not in LEAVES else []
            clean = title.replace('\r', '\\r').replace('\n', '\\n')
            if len(clean) > 120:
                clean = clean[:120] + '…'
            records.append((bool(advertised or role in CONTROLS),
                f'#{index} parent=#{parent} {role} children={len(children)} '
                f'expanded-supported={advertised} expanded-raw={raw!r} label={clean!r}'))
        # Keep actionable/header records even when there are many text leaves.
        selected = {i for i, (important, _) in enumerate(records) if important}
        for i in range(len(records)):
            if len(selected) >= 240:
                break
            selected.add(i)
        for i in sorted(selected)[:240]:
            lines.append(records[i][1])
        if len(records) > 240:
            lines.append(f'Diagnostic truncated: {len(records)} labelled controls; showing at most 240.')
        lines.append('Sidebar diagnostic complete. No controls pressed.')
        return '\n'.join(lines)

    def fresh(self):
        for attempt in range(3):
            try:
                return self.snapshot()
            except (AXError, Changed) as error:
                if isinstance(error, AXError) and error.code not in (-25202, -25204):
                    raise
                if attempt == 2:
                    raise
                self.log(f'{error} Reacquiring native references (attempt {attempt + 2} of 3)...')
                time.sleep(0.2)

    @staticmethod
    def header(entries, title):
        matches = [entry for entry in entries if entry.kind == 'header'
                   and entry.title.casefold() == title.casefold()]
        if len(matches) > 1:
            raise SidebarError('Ambiguous sidebar section: ' + title)
        return matches[0] if matches else None

    def ensure_expanded(self, entries, title):
        entry = self.header(entries, title)
        if entry is None:
            raise SidebarError('Section header not exposed: ' + title)
        # Re-read state immediately before pressing, not a cached snapshot value.
        state = expanded(entry.node)
        if state is False:
            self.log('Expanding ' + title + '...')
            entry.node.press()  # Never retry an action after an uncertain result.
            time.sleep(0.3)
            entries = self.fresh()
            entry = self.header(entries, title)
            if entry is None or entry.expanded is not True:
                raise SidebarError('Could not confirm expansion of ' + title + '. Rerun to check.')
            entries = self.settle(entries, title)
        elif state is not True:
            raise SidebarError('Could not verify ' + title + ' is expanded.')
        return entries

    @staticmethod
    def section_titles(entries, title):
        found, names = False, []
        for entry in entries:
            if entry.kind == 'header':
                if found:
                    break
                found = entry.title.casefold() == title.casefold()
            elif found and entry.kind == 'session':
                # Different rows can have identical titles, particularly
                # recurring tasks. The traversal already deduplicates AX nodes.
                names.append(entry.title)
        if not found:
            raise SidebarError('Section header not exposed: ' + title)
        return names

    @staticmethod
    def section_rows(entries, title):
        """Entries between a header and the next header: its rows and paging controls."""
        found, rows = False, []
        for entry in entries:
            if entry.kind == 'header':
                if found:
                    break
                found = entry.title.casefold() == title.casefold()
            elif found:
                rows.append(entry)
        return rows

    def settle(self, entries, title, seconds=3.0):
        """After expanding a section, wait for its rows: a project's chats load from the
        network after it opens, so an immediate read can find none. Returns once the row
        count is non-zero and unchanged across two reads, or after `seconds` (a project
        can be genuinely empty)."""
        deadline = time.monotonic() + seconds
        previous = len(self.section_rows(entries, title))
        while time.monotonic() < deadline:
            time.sleep(0.3)
            entries = self.fresh()
            count = len(self.section_rows(entries, title))
            if count and count == previous:
                return entries
            previous = count
        if not previous:
            self.log(f'{title}: no rows appeared within {seconds:.0f}s of expanding it.')
        return entries

    @staticmethod
    def project_names(entries):
        return list(dict.fromkeys(e.title for e in entries if e.kind == 'header'
                                  and e.title not in CATEGORIES and e.expanded is not None))

    @staticmethod
    def projects_more(entries):
        """The Show more control ending the Projects section: the last entry before the
        category header (e.g. Recents) that follows Projects."""
        start = next((i for i, e in enumerate(entries) if e.kind == 'header' and e.title == 'Projects'), None)
        if start is None:
            return None
        end = next((i for i in range(start + 1, len(entries))
                    if entries[i].kind == 'header' and entries[i].title in CATEGORIES), len(entries))
        return entries[end - 1] if end - 1 > start and entries[end - 1].kind == 'more' else None

    @staticmethod
    def section_more(entries, title):
        """The Show more control at the end of one project's rows, if any."""
        found, last = False, None
        for entry in entries:
            if entry.kind == 'header':
                if found:
                    break
                found = entry.title.casefold() == title.casefold()
            elif found:
                last = entry
        return last if last is not None and last.kind == 'more' else None

    def show_all(self, entries, find_more, count, what):
        """Press a list's Show more until it disappears or stops revealing rows.

        Paging only reveals rows; each press is followed by a fresh snapshot and never retried.
        """
        for _ in range(25):
            more = find_more(entries)
            if more is None:
                return entries
            before = count(entries)
            self.log(f'Showing more {what}...')
            more.node.press()
            time.sleep(0.6)
            entries = self.fresh()
            if count(entries) <= before:
                self.log(f'Show more revealed no further {what}; listing what is shown.')
                return entries
        self.log(f'Stopped showing more {what} after 25 pages.')
        return entries

    def run(self, command, argument='', project='', recents=False):
        if command == 'debug-sidebar':
            return self.debug()
        entries = self.fresh()
        if project and self.header(entries, project) is None:
            entries = self.ensure_expanded(entries, 'Projects')
            entries = self.show_all(entries, self.projects_more, lambda e: len(self.project_names(e)), 'projects')
        if command == 'projects':
            entries = self.ensure_expanded(entries, 'Projects')
            entries = self.show_all(entries, self.projects_more, lambda e: len(self.project_names(e)), 'projects')
            names = self.project_names(entries)
            if not names:
                raise SidebarError('Project rows are not exposed in this sidebar snapshot.')
            return '\n'.join(names)
        if project or recents:
            title = project or 'Recents'
            entries = self.ensure_expanded(entries, title)
            if project:
                entries = self.show_all(entries, lambda e: self.section_more(e, project),
                                        lambda e: len(self.section_titles(e, project)), f'chats in {project}')
            names = self.section_titles(entries, title)
            self.log(f'{title}: {len(names)} session rows ({len(set(names))} distinct titles).')
            if not names and not any(e.kind == 'session' for e in entries):
                raise SidebarError('No session rows were identified in the sidebar. '
                                   'Run ./agent_ctl.py --app chatgpt debug-sidebar.')
            if project:
                return '[' + project + ']\n' + '\n'.join('  ' + name for name in names)
            if not names:
                self.log('No session rows exposed under Recents.')
            return '\n'.join(names)
        sessions = [entry for entry in entries if entry.kind == 'session']
        if command == 'sessions':
            if not sessions:
                raise SidebarError('No session rows exposed in this sidebar snapshot.')
            return '\n'.join(entry.title for entry in sessions)
        exact = [entry for entry in sessions if entry.title.casefold() == argument.casefold()]
        matches = exact or [entry for entry in sessions if argument.casefold() in entry.title.casefold()]
        if len(matches) != 1:
            raise SidebarError('Session not found or title is ambiguous: ' + argument)
        matches[0].node.press()
        return 'Pressed session: ' + matches[0].title


class Modes:
    """Use the observed mode popup, then select only an identified mode menu."""
    ALIASES = {'codex': 'code', 'code': 'code', 'chatgpt': 'chat', 'chat': 'chat'}
    # Native AXMenuItem titles include the descriptive subtitle. Match the
    # observed full labels, not arbitrary items beginning with a brand name.
    MENU_LABELS = ALIASES | {
        'chatgpt create, learn, and explore': 'chat',
        'codex build, debug, and ship': 'code',
    }
    DISPLAY = {'code': 'Codex', 'chat': 'ChatGPT'}
    PREFIX = 'switch mode, current mode:'
    OPTION_ROLES = {'AXMenuItem', 'AXMenuItemRadio', 'AXRadioButton', 'AXButton'}

    def __init__(self, reader):
        self.reader = reader

    @classmethod
    def mode_for_menu_label(cls, text):
        return cls.MENU_LABELS.get(' '.join(text.split()).casefold())

    def control(self):
        for node, role, _ in self.reader.walk(self.reader.main_window(), 1600):
            if role not in ('AXPopUpButton', 'AXButton'):
                continue
            # The observed label is in AXDescription; an unrelated AXTitle must
            # not hide it, and a chat merely titled Codex is never a mode control.
            for key in ('AXDescription', 'AXTitle'):
                text = node.get(key)
                if isinstance(text, str) and text.casefold().startswith(self.PREFIX):
                    mode = self.ALIASES.get(text[len(self.PREFIX):].strip().casefold())
                    if mode is None:
                        raise SidebarError('Unrecognized mode label: ' + text)
                    return node, mode
        raise Changed('The Switch mode popup was not found. Run --app chatgpt debug-mode.')

    def menu_candidates(self, roots, limit):
        menus = {}
        seen = set()
        for root in roots:
            for node, role, ancestors in self.reader.walk(root, limit):
                if node in seen:
                    continue
                seen.add(node)
                if role == 'AXMenu':
                    menus[node] = {}
                    continue
                if role not in self.OPTION_ROLES:
                    continue
                scope = next((parent for parent in reversed(ancestors) if parent in menus), None)
                if scope is None:
                    continue
                mode = self.mode_for_menu_label(label(node))
                if mode:
                    menus[scope].setdefault(mode, []).append(node)
        return [options for options in menus.values() if set(options) == {'code', 'chat'}]

    def menu(self):
        popup, _ = self.control()
        linked = (popup.get('AXLinkedUIElements') or []) + (popup.get('AXChildren') or [])
        # The popup's explicit links and focused menu avoid scanning a long chat.
        # These AXParent values are native retained handles, never index paths.
        focused = self.reader.app.get('AXFocusedUIElement')
        for _ in range(16):
            if focused is None:
                break
            role = focused.get('AXRole')
            if role == 'AXMenu':
                linked.append(focused)
                break
            if role in ('AXWindow', 'AXApplication'):
                break
            focused = focused.get('AXParent')
        candidates = self.menu_candidates(linked, 240) if linked else []
        if not candidates:
            roots = [self.reader.main_window()]
            # Menus can also be app children or Electron portals in the window.
            roots.extend(node for node in (self.reader.app.get('AXChildren') or [])
                         if node.get('AXRole') == 'AXMenu')
            candidates = self.menu_candidates(roots, 1600)
        if len(candidates) != 1 or any(len(nodes) != 1 for nodes in candidates[0].values()):
            raise SidebarError('Cannot identify one mode menu with Codex and ChatGPT options.')
        return candidates[0]

    def run(self, wanted=''):
        popup, current = self.control()
        if not wanted:
            return current
        wanted = self.ALIASES.get(wanted.casefold())
        if wanted is None:
            raise SidebarError('Mode must be codex/code or chatgpt/chat.')
        if current == wanted:
            return 'Already in ' + self.DISPLAY[wanted]
        if expanded(popup) is not True:
            popup.press()
            time.sleep(0.3)
        # Read fresh menu handles after opening; never reuse indexed UI paths.
        try:
            options = self.menu()
        except SidebarError:
            # Capture the open menu before returning focus to the user's
            # terminal, which can dismiss it. Inspection never presses controls.
            self.reader.log('Mode selection failed; capturing the open menu now...')
            self.debug(emit=self.reader.log)
            raise
        options[wanted][0].press()
        for _ in range(10):
            time.sleep(0.2)
            try:
                _, actual = self.control()
                if actual == wanted:
                    return 'Switched to ' + self.DISPLAY[wanted]
            except AXError as error:
                if error.code not in (-25202, -25204):
                    raise
            except Changed:
                # The popup can briefly disappear while the view is replaced.
                pass
        raise SidebarError('Mode option was pressed, but the new view could not be confirmed. '
                           'Run --app chatgpt mode to check; no click was retried.')

    def debug(self, emit=None):
        """Inspect popup-linked/focused controls first, regardless of their roles.

        Diagnostics must reveal unsupported menu shapes instead of applying the
        same assumptions that caused selection to fail. Optional streaming keeps
        useful evidence available even if a later discovery scan times out.
        """
        lines, described = [], set()

        def report(message):
            lines.append(message)
            if emit:
                emit(message)

        def value(node, key):
            try:
                raw = node.get(key)
                if isinstance(raw, str):
                    return raw.replace('\r', '\\r').replace('\n', '\\n')[:180]
                return raw if isinstance(raw, (bool, int, type(None))) else '<non-scalar>'
            except SidebarError as error:
                return '<' + str(error) + '>'

        def describe(node, prefix):
            if node in described:
                return
            described.add(node)
            fields = [f'{key}={value(node, key)!r}' for key in
                      ('AXRole', 'AXSubrole', 'AXTitle', 'AXDescription', 'AXDOMIdentifier',
                       'AXExpanded', 'AXSelected', 'AXHasPopup', 'AXPopupValue')]
            if node.get('AXRole') != 'AXTextArea':
                fields.append(f'AXValue={value(node, "AXValue")!r}')
            report(prefix + ' | '.join(fields))

        roots = []
        try:
            popup, current = self.control()
            report('CURRENT MODE: ' + self.DISPLAY[current])
            describe(popup, 'POPUP: ')
            state = expanded(popup)
            report(f'POPUP EXPANDED: {state!r}')
            if state is not True:
                report('The mode menu is not reported open; reproduce the failed switch before rerunning this diagnostic.')
            roots.extend(('POPUP LINK', node) for node in (popup.get('AXLinkedUIElements') or []))
            roots.extend(('POPUP CHILD', node) for node in (popup.get('AXChildren') or []))
        except SidebarError as error:
            report('POPUP INSPECTION: ' + str(error))

        try:
            focused = self.reader.app.get('AXFocusedUIElement')
            chain = set()
            for depth in range(8):
                if focused is None or focused in chain:
                    break
                chain.add(focused)
                role = focused.get('AXRole')
                if role in ('AXWindow', 'AXApplication', 'AXWebArea', 'AXTextArea'):
                    report('FOCUS CHAIN STOP: ' + role)
                    break
                describe(focused, f'FOCUS ancestor={depth}: ')
                if depth <= 2 or role in ('AXMenu', 'AXList', 'AXListBox', 'AXPopover', 'AXDialog'):
                    roots.append((f'FOCUS ancestor={depth}', focused))
                if role in ('AXMenu', 'AXList', 'AXListBox', 'AXPopover', 'AXDialog'):
                    break
                focused = focused.get('AXParent')
        except SidebarError as error:
            report('FOCUS INSPECTION: ' + str(error))

        for name, root in roots[:8]:
            report('SUBTREE: ' + name)
            try:
                for node, role, ancestors in self.reader.walk(root, 160):
                    if role == 'AXTextArea':
                        report('SUBTREE STOP: message editor reached; draft omitted.')
                        break
                    describe(node, f'  depth={len(ancestors)} ')
            except SidebarError as error:
                report('SUBTREE INSPECTION STOPPED: ' + str(error))

        # Portals may not be explicitly linked to the popup. Show menu roles and
        # exact mode labels, never every unrelated sidebar/message button.
        report('ADDITIONAL MENU CONTROLS:')
        try:
            search_roots = [self.reader.main_window()]
            search_roots.extend(node for node in (self.reader.app.get('AXChildren') or [])
                                if node.get('AXRole') == 'AXMenu')
            count = 0
            for root in search_roots:
                for node, role, _ in self.reader.walk(root, 1600):
                    if node in described:
                        continue
                    menu_role = role.startswith('AXMenu') or role in ('AXListBox', 'AXPopover', 'AXDialog')
                    known_mode = (role in self.OPTION_ROLES | {'AXCheckBox', 'AXTab', 'AXLink'}
                                  and self.mode_for_menu_label(label(node)) is not None)
                    if menu_role or known_mode:
                        describe(node, 'CANDIDATE: ')
                        count += 1
                        if count >= 80:
                            report('Additional-menu diagnostic truncated at 80 controls.')
                            break
                if count >= 80:
                    break
        except SidebarError as error:
            report('ADDITIONAL SEARCH STOPPED: ' + str(error))
        report('Mode diagnostic complete. No controls pressed.')
        return '' if emit else '\n'.join(lines)


# While a reply streams, the composer's Send button is replaced by a stop control.
STOP_LABEL = re.compile(r'stop( streaming| generating| response)?', re.IGNORECASE)


def status(reader):
    """Read-only: current view and whether the open conversation is still responding."""
    _, mode = Modes(reader).control()
    reader.STACK_BUDGET = 20000  # The whole window, including a long conversation.
    sidebar = reader.locate()
    busy = False
    for node, role, ancestors in reader.walk(reader.main_window(), 20000):
        if role != 'AXButton' or sidebar in ancestors:
            continue  # A sidebar chat titled "Stop" is not a stop control.
        if any(STOP_LABEL.fullmatch((node.get(key) or '').strip()) for key in ('AXDescription', 'AXTitle')):
            busy = True
            break
    return f'mode: {mode}\nbusy: ' + ('yes' if busy else 'no')


def new_chat(reader, project=''):
    """Press the sidebar's New chat button, or a project's own; confirm the conversation cleared."""
    sidebar = reader.locate()
    wanted = ({f'new chat in {project}'.casefold(), f'start new chat in {project}'.casefold()}
              if project else {'new chat'})
    buttons = [node for node, role, _ in reader.walk(sidebar, 3000)
               if role == 'AXButton' and ' '.join(label(node).split()).casefold() in wanted]
    if not buttons:
        where = f'project {project!r} (expand it in the sidebar, or check the name)' if project else 'the sidebar'
        raise SidebarError('No New chat button found in ' + where + '.')
    if len(buttons) > 1:
        raise SidebarError(f'Several New chat buttons match; nothing pressed.')
    buttons[0].press()
    reader.STACK_BUDGET = 20000
    for _ in range(20):
        time.sleep(0.2)
        try:
            headings = [label(node) for node, role, _ in reader.walk(reader.main_window(), 20000)
                        if role == 'AXHeading']
        except (Changed, AXError):
            continue
        if not any(h.startswith(('You said', 'ChatGPT said')) for h in headings):
            return 'Opened a new chat' + (f' in {project}' if project else '') + '.'
    raise SidebarError('Pressed New chat, but the previous conversation is still shown. Check the app; '
                       'nothing was retried.')


# Reading and typing through native AX. The earlier System Events versions walked the
# window element by element and took up to a minute on a long conversation; a native
# walk of the same window (2,300 elements) takes about 0.1 s.
REPLY_HEADING = re.compile(r'(chatgpt|assistant) (said|responded)', re.IGNORECASE)
TURN_HEADING = re.compile(r'(you|chatgpt|assistant) (said|responded)', re.IGNORECASE)
# The composer's label doubles as its placeholder and varies with context ("Do anything"
# in Codex, "Ask ChatGPT" in a new chat, "Work with ChatGPT" in some conversations).
COMPOSER_LABEL = re.compile(r'(do anything|(ask|work with|message|reply to) .+)', re.IGNORECASE)


def window_rows(reader):
    reader.STACK_BUDGET = 60000
    return list(reader.walk(reader.main_window(), 60000))


def latest_reply(reader):
    """Text of the latest assistant reply: the static text after the last reply heading."""
    rows = window_rows(reader)
    start = next((i for i in range(len(rows) - 1, -1, -1)
                  if rows[i][1] == 'AXHeading' and REPLY_HEADING.match(label(rows[i][0]))), None)
    if start is None:
        raise SidebarError('Cannot identify an assistant reply. Run --app chatgpt debug-read.')
    heading = label(rows[start][0])
    # The composer is the window's last text area. Any other text area inside the reply is a
    # document card ("Writing", with an "Open editor" button) that holds its text as AXValue.
    composer = next((row[0] for row in reversed(rows) if row[1] == 'AXTextArea'), None)
    lines = []
    for index in range(start + 1, len(rows)):
        node, role, _ = rows[index]
        if role == 'AXToolbar':
            break  # The reply's action bar.
        if role == 'AXTextArea':
            if node == composer or COMPOSER_LABEL.fullmatch((node.get('AXDescription') or '').strip()):
                break
            value = node.get('AXValue')
            if isinstance(value, str) and value.strip():
                lines.append(value.strip())
            continue
        text = label(node)
        # Rate/fork end the message; Copy also appears inside code blocks, so it does not.
        if role == 'AXButton' and text in ('Rate response', 'Fork chat from here'):
            break
        if role == 'AXHeading' and TURN_HEADING.match(text):
            break  # The next turn; headings inside the reply itself continue.
        if role == 'AXStaticText':
            value = node.get('AXValue')
            if isinstance(value, str) and value and not (index == start + 1 and value == heading):
                lines.append(value)
    if not lines:
        raise SidebarError('Assistant heading found, but no reply text exposed. Run --app chatgpt debug-read.')
    return '\n'.join(lines)


def find_composer(reader):
    editors = [node for node, role, _ in window_rows(reader)
               if role == 'AXTextArea' and COMPOSER_LABEL.fullmatch((node.get('AXDescription') or '').strip())]
    if not editors:
        raise SidebarError('Could not find the ChatGPT/Codex message input. Nothing entered.')
    return editors[-1]  # The conversation's composer comes last in the window.


def draft_of(editor):
    """The composer's text, or '' when empty (an empty editor reports '\\n' + its label)."""
    value = (editor.get('AXValue') or '').strip('\n')
    placeholder = (editor.get('AXDescription') or '').strip()
    return '' if value in ('', placeholder) else value


KEYS = '''
tell application id "com.openai.codex" to activate
delay 0.1
tell application "System Events"
    set frontBundle to bundle identifier of first application process whose frontmost is true
end tell
if frontBundle is not "com.openai.codex" then error "ChatGPT is not frontmost; no keys were sent."
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


def focus_composer(reader):
    """Focus the composer and confirm it. Focus lands a moment after it is set, and right
    after the app comes forward or a session opens the composer may still be replaced, so
    retry with a fresh reference for up to ~2 s. Setting focus has no other effect."""
    for _ in range(12):
        try:
            editor = find_composer(reader)
            editor.set_bool('AXFocused', True)
            time.sleep(0.15)
            if editor.get('AXFocused') is True:
                return editor
        except (Changed, AXError):
            time.sleep(0.15)
    raise SidebarError('Could not focus the message input; nothing entered.')


def input_text(reader, command, text):
    """type/send paste into the empty composer and verify; send/enter press Return and
    confirm the composer cleared. Nothing is ever retried after a keystroke."""
    editor = reader.read(lambda: find_composer(reader))
    if command in ('type', 'send'):
        if draft_of(editor):
            raise SidebarError('Prompt already contains a draft; send or clear it first.')
    elif not draft_of(editor):
        raise SidebarError('Prompt is empty; nothing submitted.')
    focus_composer(reader)
    if command in ('type', 'send'):
        keys('paste', text)
        expected = ' '.join(text.split())
        for _ in range(40):
            try:
                if ' '.join(draft_of(find_composer(reader)).split()) == expected:
                    break
            except (Changed, AXError):
                pass
            time.sleep(0.1)
        else:
            raise SidebarError('Could not verify pasted text; nothing submitted. The text may be in the input.')
        if command == 'type':
            return 'Typed and verified.'
        focus_composer(reader)
    keys('return')
    for _ in range(40):
        time.sleep(0.1)
        try:
            if not draft_of(find_composer(reader)):
                return 'Submitted; prompt cleared.'
        except (Changed, AXError, SidebarError):
            pass  # The composer can be replaced while the message is sent.
    raise SidebarError('Return pressed, but the prompt did not clear. Check the conversation before '
                       'retrying; the message may already be sent.')


ACTIVATE = '''
tell application id "com.openai.codex" to activate
tell application "System Events"
    set p to first application process whose bundle identifier is "com.openai.codex"
    set frontmost of p to true
    return unix id of p
end tell
'''


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) != 1 or args[0] not in ('sessions', 'projects', 'session', 'debug-sidebar', 'mode', 'debug-mode',
                                         'status', 'new', 'read', 'type', 'send', 'enter', 'return'):
        print('This helper is called by agent_ctl.py --app chatgpt.', file=sys.stderr)
        return 2
    try:
        api = NativeAX()
        if not api.ax.AXIsProcessTrusted():
            raise SidebarError('Native accessibility access is not enabled for this terminal. '
                               'Enable your terminal in System Settings > Privacy & Security > Accessibility, '
                               'then restart the terminal and rerun.')
        result = subprocess.run(['osascript', '-e', ACTIVATE], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise SidebarError('Could not activate ChatGPT: ' + result.stderr.strip())
        app = api.application(int(result.stdout.strip()))
        time.sleep(0.5)
        reader = Sidebar(app)
        if args[0] == 'mode':
            result = Modes(reader).run(os.environ.get('CTL_ARG', ''))
        elif args[0] == 'debug-mode':
            result = Modes(reader).debug(emit=reader.log)
        elif args[0] == 'status':
            result = reader.read(lambda: status(reader))
        elif args[0] == 'new':
            result = new_chat(reader, os.environ.get('CTL_PROJECT', ''))
        elif args[0] == 'read':
            result = reader.read(lambda: latest_reply(reader))
        elif args[0] in ('type', 'send', 'enter', 'return'):
            result = input_text(reader, 'enter' if args[0] == 'return' else args[0], os.environ.get('CTL_ARG', ''))
        else:
            result = reader.run(args[0], os.environ.get('CTL_ARG', ''),
                                os.environ.get('CTL_PROJECT', ''), os.environ.get('CTL_RECENTS') == '1')
        print(result)
        return 0
    except (SidebarError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Sidebar lookup cancelled.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
