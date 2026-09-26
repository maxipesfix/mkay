# Multi-agent desktop CLI

Control the Claude desktop app, the combined ChatGPT/Codex app, and Cursor from a
macOS terminal: switch views, list projects and sessions, open a session, read its
latest reply, and type or submit a prompt. `agent_ctl.py` is the entry point for
every app. The MCP server in [`../multi-agent-mcp`](../multi-agent-mcp/README.md)
exposes the same commands to local and remote LLM clients.

## App versions

| App (tested views) | Version | Build | Bundle ID |
| --- | --- | --- | --- |
| ChatGPT (ChatGPT and Codex) | `26.915.31945` | `9922` | `com.openai.codex` |
| Claude (Chat/Cowork and Code) | `2.9939.2` | `2.9939.2` | `com.anthropic.claudefordesktop` |
| Cursor (Agents and IDE) | `3.21.18` | `3.21.18` | `com.todesktop.230313mzl4w4u92` |

Versions were read from the installed apps on 2026-09-24 (Claude on 2026-09-25, after
it updated from `2.7032.0`). The live navigation tests below ran on the earlier Claude
version; on `2.9939.2` the `mode`, `projects` and `status` commands and the MCP tools
were checked. App updates can change the accessibility tree these scripts rely on; if
a command starts failing after an update, run the matching diagnostic and compare.

Claude `2.9939.2`, like Cursor, rejects the `AXEnhancedUserInterface` flag that
exposes an Electron app's full interface; the scripts then fall back to
`AXManualAccessibility`.

## Setup

- macOS with Python 3 and the target desktop app installed and signed in.
- Enable Accessibility for the terminal running the scripts. Allow Automation
  access to System Events and the target app when macOS requests it. Restart the
  terminal after changing permissions if access is still unavailable.
- Cursor exposes its interface only after the scripts turn on its accessibility
  support. Cursor may then offer to enable `editor.accessibilitySupport` ("Screen
  reader usage detected"); either answer works for these scripts.
- No third-party Python packages are required. The scripts use `osascript` and
  native macOS accessibility frameworks.

```bash
cd /Users/maxim/susurobo/code/mkay/multi-agent-cli
./agent_ctl.py mode
```

`./agent_ctl.py` only works from this directory. From anywhere else, use the full
path, for example
`/Users/maxim/susurobo/code/mkay/multi-agent-cli/agent_ctl.py --app cursor mode`.

Commands bring the target app forward. Leave it untouched while a command or test
is running. Keep its sidebar open for session navigation and listing.

## Files

```text
multi-agent-cli/
  README.md
  agent_ctl.py                  # Entry point: --app claude|chatgpt|cursor (Claude is the default)
  ax_native.py                  # Shared native macOS accessibility binding (NativeAX, Walker)
  backends/
    __init__.py                 # App registry and each app's two views
    claude.py                   # Claude entry point, mode switching and AppleScript diagnostics
    claude_sidebar.py           # Claude lists, status, read, input and session opening (native AX)
    chatgpt.py                  # Combined ChatGPT/Codex entry point and AppleScript diagnostics
    chatgpt_sidebar.py          # Combined-app mode, sidebar, read and input (native AX)
    cursor.py                   # Cursor: every command through native AX
  test/
    navigation.py               # Shared live navigation test runner
    test_claude_navigation.py   # Live Claude test
    test_chatgpt_navigation.py  # Live ChatGPT/Codex test
    test_cursor_navigation.py   # Live Cursor test
```

Keep these files together. The test runners locate `../agent_ctl.py` relative to
their own location, so they work from any working directory. `claude_ctl.py`,
`chatgpt_ctl.py` and the sidebar modules that used to sit beside it have moved
into `agent_ctl.py` and `backends/`.

`./agent_ctl.py apps --json` prints the apps and their views as JSON. Programs such
as the MCP server use it instead of importing the backends.

## App and mode selection

Put `--app` before the command. Omitting it selects Claude. The flag uses **two
dashes**. `./agent_ctl.py --help` lists the apps and their views;
`./agent_ctl.py --app APP help` lists an app's commands.

| App | Command | Result |
| --- | --- | --- |
| Claude | `./agent_ctl.py --app claude mode` | Prints `chat` or `code` |
| Claude | `./agent_ctl.py --app claude mode chat` | Switches to Chat/Cowork |
| Claude | `./agent_ctl.py --app claude mode code` | Switches to Code |
| Combined app | `./agent_ctl.py --app chatgpt mode` | Prints `chat` or `code` |
| Combined app | `./agent_ctl.py --app chatgpt mode chatgpt` | Switches to ChatGPT |
| Combined app | `./agent_ctl.py --app chatgpt mode codex` | Switches to Codex |
| Cursor | `./agent_ctl.py --app cursor mode` | Prints `agents` or `ide` |
| Cursor | `./agent_ctl.py --app cursor mode agents` | Switches to the Agents window |
| Cursor | `./agent_ctl.py --app cursor mode ide` | Switches to an IDE window |

Claude also accepts `cowork` and `claude` as aliases for `chat`. The combined app
also accepts `chat` and `code` as mode names. Cursor also accepts `agent` and
`editor`. `--app codex` is not supported: select `--app chatgpt`, then `mode codex`.

## Command reference

Every command is `./agent_ctl.py --app APP COMMAND [ARGS]`. Quote names and
messages that contain spaces.

| Command | Claude | ChatGPT/Codex | Cursor |
| --- | --- | --- | --- |
| `mode [VIEW]` | ✅ | ✅ | ✅ |
| `projects` | ✅ | ✅ | ✅ |
| `sessions` | ✅ | ✅ | ✅ |
| `sessions --project "NAME"` | ✅ | ✅ | ✅ |
| `sessions --recents` | ✅ | ✅ | Refused (exit 2) |
| `status` | ✅ | ✅ | ✅ |
| `new [--project NAME]` | — | ✅ | — |
| `session "TITLE"` | ✅ | ✅ | ✅ |
| `project "NAME"` | — | — | ✅ |
| `read` | ✅ | ✅ | ✅ |
| `type "TEXT"` | ✅ | ✅ | ✅ |
| `send "TEXT"` | ✅ | ✅ | ✅ |
| `enter` (alias `return`) | ✅ | ✅ | ✅ |
| `answer LETTER ["TEXT"]` | — | — | ✅ |

| Command | What it does |
| --- | --- |
| `projects` | Lists project names in the current view; see **Listing scope** |
| `sessions` | Lists session names in the current view |
| `sessions --project "NAME"` | Lists sessions in one project; expands it when needed |
| `sessions --recents` | Lists the current view's recent/unassigned sessions |
| `session "TITLE"` | Opens a session matching the title |
| `read` | Prints the latest assistant reply in the selected conversation |
| `status` | Read-only: view, whether the open conversation is still working, and more; see below |
| `new [--project "NAME"]` | ChatGPT/Codex: opens a new, empty conversation (inside the project if given) by pressing the sidebar's New chat button, and checks that the old conversation cleared |
| `type "TEXT"` | Pastes and verifies text in the empty prompt box without submitting |
| `send "TEXT"` | Pastes and verifies text, then presses Return to submit |
| `enter` | Submits the existing draft |

Session titles are printed one per line, including duplicates. Project session
output begins with `[NAME]` and indents each session by two spaces, for example:

```text
[vrm-lipsync]
  Code submission review
```

`--project` and `--recents` cannot be combined.

These are app sessions/conversations, not browser tabs. Reading and writing act on
the selected conversation; open it with `session` first. There is no session ID or
row-index selector. Claude uses the first matching title substring; the combined
app and Cursor prefer an exact title and otherwise require a unique substring
match. Identically named sessions can therefore require manual selection.

### Output and exit status

Results go to stdout. Progress and status messages, such as
`Cursor ide: read 1 session rows.`, go to stderr, so a caller that reads stdout
gets only the result.

| Exit status | Meaning |
| --- | --- |
| `0` | Success |
| `1` | The command ran but failed or refused, for example a title that matched nothing |
| `2` | Invalid usage, for example an unknown command or a missing argument |
| `130` | Interrupted |

### Status

`status` prints `key: value` lines and never changes anything:

```text
mode: code
busy: yes
running: MCP remote control macOS tests
unread: Background noise interruptions in Nexor-Pipecat
```

| Key | Apps | Meaning |
| --- | --- | --- |
| `mode` | All | Current view, as printed by `mode` |
| `busy` | All | `yes` while the open conversation's agent is working, detected by the stop control that replaces Send; Cursor prints `unknown` when its main window has no agent composer |
| `question` | Cursor | `yes` while a multiple-choice question is pending |
| `agent_error` | Cursor | The text of an error card shown above the composer instead of a reply (for example `Invalid API key. Unauthorized User API key Request ID: …`) |
| `running` | Claude | One line per sidebar session still working |
| `unread` | Claude | One line per sidebar session with an unread reply |

The busy signal was confirmed live for Claude `2.9939.2` (its composer shows a
**Stop** button while responding) and ChatGPT (`busy: yes` while a reply streamed,
then `no`). Cursor was only checked idle; its busy state assumes the stop control
that replaces **Send message** while generating (labels such as `Stop` or
`Stop generating`, optionally with a shortcut) and has not been observed live yet.

Run other UI automation against an app while a command is working on it and the
command can stall; the MCP server avoids this by running one command at a time.

### Listing scope

| View | `projects` | `sessions` | `sessions --recents` |
| --- | --- | --- | --- |
| Claude Chat/Cowork | Projects page, including unpinned projects | Native **Chats and tasks** sidebar list | Same native list; can include sessions belonging to projects |
| Claude Code | Folder names, excluding **No folder** | Sessions across folders and **No folder**, expanding available “Show N more” controls | Sessions under **No folder** |
| ChatGPT or Codex | Project headers exposed in the selected view's sidebar | Exposed sidebar session rows across sections | Rows under the **Recents** section |
| Cursor Agents | Repositories under the sidebar's **Repositories** section | Agent sessions across all repositories, expanding collapsed ones | Not supported |
| Cursor IDE | Workspace names of the open IDE windows | Open agent chat tabs across all IDE windows | Not supported |

Claude Chat project listing opens the Projects page; listing a project's sessions
opens that project page. This can change what is shown in the main pane.
Listings reflect exposed app content, not a complete account-history export.

In ChatGPT and Codex, `projects` presses the **Show more** at the end of the Projects
list until every project is shown, and `sessions --project NAME` presses that
project's own **Show more** until all its chats are listed (at most 25 presses each;
paging stops as soon as a press reveals nothing new). Pressing Show more leaves the
lists expanded in the app. Paging controls and each project's "New chat in …" button
are never reported as sessions. The unfiltered `sessions` and `sessions --recents`
still list only the rows already shown. Titles can contain HTML entities such as
`&amp;`. A passing navigation test does not prove that every returned name is a
conversation.

### Read, type, and submit

```bash
./agent_ctl.py --app claude session "VRM viseme"
./agent_ctl.py --app claude read
./agent_ctl.py --app claude type "Reply only: SCRIPT_OK"
./agent_ctl.py --app claude enter
# After the assistant finishes:
./agent_ctl.py --app claude read
```

Or type and submit in one command:

```bash
./agent_ctl.py --app chatgpt send "Reply only: SCRIPT_OK"
```

`type` and `send` refuse to overwrite an existing draft; clear or submit it first.
`enter` refuses an empty prompt. Input uses clipboard paste and restores the
clipboard afterward. `send` presses Return only after the pasted text is verified;
if verification fails, nothing is submitted, but the text may already be in the
prompt box. Claude's `Sent.` means verified text followed by Return; the combined
app and Cursor also check that the prompt clears. No app waits for a completed
assistant response.

Claude, ChatGPT and Codex read replies and enter text through native accessibility
calls: a reply reads in about a second (Claude about half a second), where the earlier
System Events versions took several seconds to a minute on long conversations.
Claude's `session` also opens sidebar rows natively and falls back to a whole-window
search only for titles that are not in the sidebar, such as links on a project page.
Before pasting, the input is focused and the focus confirmed, retrying for up to two
seconds while the app comes forward or a newly opened conversation renders. `read` may return partial text while the reply is streaming,
and its line breaks reflect accessibility text fragments.

If submission times out or cannot be confirmed, inspect the app before retrying
to avoid sending the message twice.

## Cursor

Cursor's two views are separate windows. The view is whichever Cursor window is
main: **Cursor Agents** is the Agents view, and any workspace window is the IDE
view. Every Cursor command uses native accessibility calls; AppleScript is used
only to bring Cursor forward, paste, and press Return.

### Cursor command reference

| Command | Agents view | IDE view |
| --- | --- | --- |
| `mode` | Prints `agents` | Prints `ide` |
| `status` | View, busy, and pending question for the Agents window's chat | Same, for the main IDE window's agent chat |
| `mode ide` / `mode agents` | Presses Cursor's **IDE** button; Cursor picks the IDE window | Presses **Agents Window** |
| `projects` | Repository names from the sidebar | Workspace names of the open IDE windows |
| `project "NAME"` | Switches to the IDE view on that workspace's window | Brings that workspace's window forward |
| `sessions` | Agent sessions across all repositories | Open agent chat tabs across all IDE windows |
| `sessions --project "NAME"` | Agent sessions in that repository | Open agent chat tabs in that workspace's window |
| `session "TITLE"` | Opens the agent session | Brings its window forward and selects its chat tab |
| `read` | Latest reply in the open agent chat | Latest reply in the main window's agent chat |
| `type` / `send` / `enter` | Uses the open chat's composer | Uses the main window's agent composer |
| `answer LETTER ["TEXT"]` | Answers the pending question | Answers the pending question |
| `debug-mode` | Windows, main window, and view switch buttons | Same |
| `debug-sidebar` | Sidebar controls | Windows and their chat tabs |

Examples:

```bash
./agent_ctl.py --app cursor mode ide
./agent_ctl.py --app cursor projects
./agent_ctl.py --app cursor project recursive
./agent_ctl.py --app cursor sessions --project vrm-lipsync
./agent_ctl.py --app cursor session "Code submission review"
./agent_ctl.py --app cursor read
./agent_ctl.py --app cursor answer B
./agent_ctl.py --app cursor mode agents
```

### Navigating IDE windows

Several IDE windows can be open, one per workspace. `mode ide` leaves the choice
to Cursor, which brings forward the most recently used IDE window. To pick one,
list the workspaces and bring one forward:

```bash
./agent_ctl.py --app cursor projects
./agent_ctl.py --app cursor project recursive
```

`project` raises that workspace's window and confirms it became the main window;
from the Agents view it also switches to the IDE view. It only reaches windows that
are already open, and refuses a name matching no window or several. Workspaces are
matched on the part of the window title after “—”. In the IDE view,
`session "title"` also brings forward the window holding that chat tab.

### Listing details

- Agents session titles have the status icon and the age (such as `2d`) removed.
  Listing expands collapsed repositories and leaves them expanded.
- Cursor's own **Projects** sidebar section (Cursor projects with their own
  project page) is not listed.
- In the IDE view only open agent chat tabs are listed. File tabs, and past chats
  behind **Show Chat History**, are not.
- Cursor has no Recents list, so `sessions --recents` exits with status 2.

### Reading replies

`read` returns everything after the latest user message, including tool-call
summaries such as `Explored 1 search, ran 2 commands`. Cursor only exposes the chat
rows it has rendered, so in a long conversation whose latest user message is
scrolled out of view, `read` returns every rendered row, which can include older
turns. If several agent chats are visible in one IDE window, commands refuse to
guess; close or focus one.

The composer's text is read from its rendered paragraphs, not from the editor's
`AXValue`, which can keep showing old text for seconds after the editor is cleared or
new text is pasted. That stale value made the first `send` attempts fail their
verification even though the text had been pasted.

### Error cards

When a request fails, for example with an invalid API key, Cursor shows an error card
above the composer (with **Dismiss error**) instead of adding a reply to the
conversation. `status` reports it as `agent_error: …`, and `read` ends with an
`Agent error: …` line. The card stays until it is dismissed in Cursor.

### Multiple-choice questions

When a Cursor agent is waiting on a multiple-choice question, `read` ends with the
question and its lettered options:

```text
Pending question: What would you like me to do?
  A. Set up the GitHub remote and push the code
  B. Do a full code review of the repo and report findings
  F. Other...
```

While a question is pending, `send` and `enter` refuse to submit, because the
composer then only adds optional details to the answer. `type` still works.
Answer with `answer`:

```bash
./agent_ctl.py --app cursor answer B
./agent_ctl.py --app cursor answer F "Push to the OpenHRIai GitHub org"
```

Pass text only for a free-text option such as **Other**. `answer` presses the
option; Cursor treats that click as a selection, so `answer` then presses
**Continue** once. It prints `done` when the question panel closes, or the next
pending question in the same `Pending question:` format when Cursor asks several,
so a caller can repeat `answer` until it gets `done` and then `read` the reply.
Invalid letters and missing or unexpected text are refused before anything is
pressed. If the answer cannot be confirmed, check Cursor before retrying; nothing
is retried automatically.

### Cursor verification status

| Behavior | Status |
| --- | --- |
| `mode`, `projects`, `project`, `sessions`, `session`, `read` in both views | Tested live |
| `enter` refusing an empty prompt or a pending question | Tested live |
| `answer` with a normal option in the Agents view | Tested live |
| `type`, and `send` refusing a draft | Tested live (Agents view) |
| `send` submitting | Not yet confirmed live |
| `answer` with a free-text option, in the IDE view, or with several questions | Not tested live |

## Diagnostics

| Command suffix | Claude | Combined ChatGPT/Codex | Cursor |
| --- | --- | --- | --- |
| `debug-mode` | Mode radio buttons and values | Current mode popup and exposed menu options | Windows, main window, and view switch buttons |
| `debug-sidebar` | Sidebar and primary-pane controls | Sidebar structure and recognized rows | Agents: sidebar controls; IDE: windows and chat tabs |
| `debug-sessions` | Button titles/descriptions for session lookup | Not supported | Not supported |
| `debug-ui` | Not supported | Bounded general control scan | Not supported |
| `debug-focus` | Not supported | Prompts you to click the input within eight seconds, then inspects it | Not supported |
| `debug-input` | Not supported | Raw input value, length, and character codes | Not supported |
| `debug-read` | Not supported | Controls and short text excerpts around the latest reply | Not supported |

For example:

```bash
./agent_ctl.py --app claude debug-mode
./agent_ctl.py --app chatgpt debug-sidebar
./agent_ctl.py --app cursor debug-mode
```

Diagnostics only read; they never press controls. They can print conversation
text or draft content. The input diagnostic's `'\nDo anything'` (Codex) or
`'\nAsk ChatGPT'` (ChatGPT) value is the known empty-editor placeholder, not a draft.

## Live navigation tests

From this directory:

```bash
./test/test_claude_navigation.py
./test/test_chatgpt_navigation.py
./test/test_cursor_navigation.py
```

Or from anywhere:

```bash
/Users/maxim/susurobo/code/mkay/multi-agent-cli/test/test_claude_navigation.py
/Users/maxim/susurobo/code/mkay/multi-agent-cli/test/test_chatgpt_navigation.py
/Users/maxim/susurobo/code/mkay/multi-agent-cli/test/test_cursor_navigation.py
```

Each test reads the starting mode, switches through both modes, lists projects,
lists sessions for every returned project, lists Recents, and verifies the active
mode. The Claude and Cursor tests also run unfiltered `sessions` in each mode. The
Cursor test skips Recents, which Cursor does not have. Each test restores and
verifies the starting mode on normal completion, including reported command
failures. Interrupting it may leave the mode changed.

The Claude test switches the Claude app between Chat and Code, including any Claude
Code session you are working in; run it from a separate terminal.

The terminal output and saved report include **project and session names**, retain
duplicate titles, and show every command's stdout, stderr, exit status, and elapsed
time. A command has a 90-second test limit; the whole run can take several minutes.
These tests exercise navigation and listing, not `project`, message input and
submission, `read`, or `answer`.

Last results, all on 2026-09-24 with the versions above:

| Test | Result |
| --- | --- |
| Claude | PASS, 38 commands, 0 failures |
| ChatGPT/Codex | PASS, 22 commands, 0 failures |
| Cursor | PASS, 20 commands, 0 failures |

Reports are created in the working directory as `APP-navigation-TIMESTAMP.txt`,
for example `cursor-navigation-TIMESTAMP.txt`. Existing reports are never
overwritten. Options:

```bash
./test/test_claude_navigation.py --help
./test/test_claude_navigation.py --report /tmp/claude-navigation-check.txt
./test/test_claude_navigation.py --app cursor
./test/test_claude_navigation.py --cli /path/to/agent_ctl.py
```

Exit status `0` means the navigation commands and mode checks passed; nonzero
means a failure. `130` indicates an interrupted run. A pass is not a visual check
of every title or proof that the app exposed its complete history.
