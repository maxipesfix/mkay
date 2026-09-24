"""ChatGPT/Codex combined-app backend. Live UI behavior must be verified by the user."""
import os
import subprocess
import sys
import unicodedata

from backends import helper

COMMON = r'''
on attr(el, key)
    tell application "System Events"
        try
            set v to value of attribute key of el
            if v is missing value then return ""
            return v as text
        on error errText number errNum
            -- A stale reference is not a missing optional label.
            if errNum is -1719 and errText contains "Invalid index" then error errText number errNum
            return ""
        end try
    end tell
end attr

on labelOf(el)
    repeat with key in {"AXTitle", "AXDescription", "AXValue"}
        set t to my attr(el, key as text)
        if t is not "" then return t
    end repeat
    return ""
end labelOf

on mainWindow(appProcess)
    with timeout of 2 seconds
        tell application "System Events"
            set matches to windows of appProcess whose name is "ChatGPT"
        end tell
    end timeout
    if (count matches) is not 1 then error "Cannot identify one ChatGPT main window. Close duplicate main windows and retry."
    return item 1 of matches
end mainWindow

-- Resolve the installed combined app by its observed bundle identifier.
tell application id "com.openai.codex" to activate
tell application "System Events"
    set appProcess to first application process whose bundle identifier is "com.openai.codex"
    set frontmost of appProcess to true
    tell appProcess
        try
            set enhanced to false
            try
                set enhanced to value of attribute "AXEnhancedUserInterface" as boolean
            end try
            if not enhanced then set value of attribute "AXEnhancedUserInterface" to true
        end try
    end tell
end tell
delay 0.8
tell application "System Events"
    tell appProcess
        if (count windows) is 0 then error "ChatGPT has no open window."
        set win to my mainWindow(appProcess)
        set elems to entire contents of win
    end tell
end tell
'''

# Debugging avoids the unbounded `entire contents` query used by operations.
DEBUG_COMMON = COMMON.replace("        set elems to entire contents of win", "        set elems to {}")
DEBUG = r'''
on describeControl(el, prefixText)
    with timeout of 1 second
        set r to my attr(el, "AXRole")
        -- Do not dump message bodies or draft contents.
        set rowText to prefixText & r & " | description=" & my attr(el, "AXDescription") & " | id=" & my attr(el, "AXIdentifier") & " | placeholder=" & my attr(el, "AXPlaceholderValue")
        if r is "AXRadioButton" or r is "AXTab" or r is "AXButton" or r is "AXPopUpButton" then set rowText to rowText & " | title=" & my attr(el, "AXTitle")
        if r is "AXRadioButton" or r is "AXTab" then set rowText to rowText & " | value=" & my attr(el, "AXValue") & " | selected=" & my attr(el, "AXSelected")
        log rowText
    end timeout
end describeControl

log "Focused control (click the message box before starting this diagnostic):"
try
    with timeout of 1 second
        tell application "System Events" to set focusedEl to value of attribute "AXFocusedUIElement" of appProcess
    end timeout
    my describeControl(focusedEl, "FOCUSED: ")
    repeat 3 times
        with timeout of 1 second
            tell application "System Events" to set focusedEl to value of attribute "AXParent" of focusedEl
        end timeout
        my describeControl(focusedEl, "PARENT: ")
    end repeat
on error errText
    log "Focused-control inspection stopped: " & errText
end try

log "Bounded window scan (up to 100 controls / 10 seconds):"
set started to current date
set queue to {win}
set cursorIndex to 1
repeat while cursorIndex <= (count queue) and cursorIndex <= 100
    if ((current date) - started) >= 10 then exit repeat
    set el to item cursorIndex of queue
    set cursorIndex to cursorIndex + 1
    try
        my describeControl(el, "")
        with timeout of 1 second
            tell application "System Events" to set children to UI elements of el
        end timeout
        repeat with childEl in children
            if (count queue) >= 100 then exit repeat
            set end of queue to contents of childEl
        end repeat
    on error errText
        log "Skipped control: " & errText
    end try
end repeat
log "Diagnostic complete; scanned " & (cursorIndex - 1) & " controls."
return ""
'''

FOCUS = r'''
on describeEditor(el, prefixText)
    with timeout of 1 second
        log prefixText & "role=" & my attr(el, "AXRole") & " | subrole=" & my attr(el, "AXSubrole") & " | description=" & my attr(el, "AXDescription") & " | dom-id=" & my attr(el, "AXDOMIdentifier") & " | placeholder=" & my attr(el, "AXPlaceholderValue")
        try
            tell application "System Events" to set valueSettable to settable of attribute "AXValue" of el
            log "AXValue settable=" & (valueSettable as text)
        end try
        log "AXValue length=" & (length of my attr(el, "AXValue"))
    end timeout
end describeEditor

log "CLICK THE MESSAGE BOX NOW. You have 8 seconds. Do not switch back to Terminal until the scan finishes."
delay 8
with timeout of 2 seconds
    tell application "System Events" to set focusedEl to value of attribute "AXFocusedUIElement" of appProcess
end timeout
my describeEditor(focusedEl, "FOCUSED: ")
set parentEl to focusedEl
repeat 2 times
    try
        with timeout of 1 second
            tell application "System Events" to set parentEl to value of attribute "AXParent" of parentEl
        end timeout
        my describeEditor(parentEl, "PARENT: ")
    end try
end repeat
try
    with timeout of 1 second
        tell application "System Events" to set children to UI elements of focusedEl
    end timeout
    set childCount to 0
    repeat with childEl in children
        if childCount >= 8 then exit repeat
        my describeEditor(childEl, "CHILD: ")
        set childCount to childCount + 1
    end repeat
end try
log "Focused scan complete. No text entered or submitted."
return ""
'''

DEBUG_READ = r'''
on brief(valueText)
    if (length of valueText) > 180 then return (text 1 thru 180 of valueText) & "..."
    return valueText
end brief
on describeReplyNode(el, prefixText)
    with timeout of 1 second
        log prefixText & my attr(el, "AXRole") & " | title=" & my brief(my attr(el, "AXTitle")) & " | description=" & my brief(my attr(el, "AXDescription")) & " | value=" & my brief(my attr(el, "AXValue")) & " | dom-id=" & my attr(el, "AXDOMIdentifier")
    end timeout
end describeReplyNode
set headingIndex to 0
repeat with i from (count elems) to 1 by -1
    set el to item i of elems
    if my attr(el, "AXRole") is "AXHeading" then
        set t to my labelOf(el)
        ignoring case
            if t starts with "ChatGPT said" or t starts with "ChatGPT responded" or t starts with "Assistant said" or t starts with "Assistant responded" then
                set headingIndex to i
                exit repeat
            end if
        end ignoring
    end if
end repeat
if headingIndex is 0 then error "No assistant heading found."
set headingEl to item headingIndex of elems
my describeReplyNode(headingEl, "LATEST HEADING: ")
set ancestor to headingEl
repeat 3 times
    with timeout of 1 second
        tell application "System Events" to set ancestor to value of attribute "AXParent" of ancestor
    end timeout
    my describeReplyNode(ancestor, "ANCESTOR: ")
end repeat
set stopIndex to headingIndex + 45
if stopIndex > (count elems) then set stopIndex to count elems
set started to current date
repeat with i from (headingIndex + 1) to stopIndex
    if ((current date) - started) >= 10 then exit repeat
    set el to item i of elems
    set r to my attr(el, "AXRole")
    if r is "AXTextArea" then
        log "STOP: composer reached (draft omitted)."
        exit repeat
    end if
    if r is "AXHeading" then
        set t to my labelOf(el)
        if t starts with "You said" then
            log "STOP: next user message reached."
            exit repeat
        end if
    end if
    my describeReplyNode(el, "AFTER HEADING: ")
end repeat
log "Reply diagnostic complete."
return ""
'''

READ = r'''
-- ChatGPT's heading and body are siblings, not one heading-parent subtree.
set headingIndex to 0
set headingLabel to ""
repeat with i from (count elems) to 1 by -1
    set el to item i of elems
    if my attr(el, "AXRole") is "AXHeading" then
        set t to my labelOf(el)
        ignoring case
            if t starts with "ChatGPT said" or t starts with "ChatGPT responded" or t starts with "Assistant said" or t starts with "Assistant responded" then
                set headingIndex to i
                set headingLabel to t
                exit repeat
            end if
        end ignoring
    end if
end repeat
if headingIndex is 0 then error "Cannot identify an assistant reply. Run --app chatgpt debug-read."
set output to ""
if headingIndex < (count elems) then
    repeat with i from (headingIndex + 1) to (count elems)
        set el to item i of elems
        set r to my attr(el, "AXRole")
        -- Rate/fork controls terminate the message; Copy also occurs INSIDE code
        -- blocks and must not terminate reading.
        if r is "AXTextArea" or r is "AXToolbar" then exit repeat
        if r is "AXButton" then
            set t to my labelOf(el)
            if t is "Rate response" or t is "Fork chat from here" then exit repeat
        end if
        if r is "AXHeading" then
            set t to my labelOf(el)
            ignoring case
                if t starts with "You said" or t starts with "ChatGPT said" or t starts with "ChatGPT responded" or t starts with "Assistant said" or t starts with "Assistant responded" then exit repeat
            end ignoring
        end if
        if r is "AXStaticText" then
            set t to my attr(el, "AXValue")
            -- Skip only the heading's immediate accessibility text duplicate.
            if not (i is (headingIndex + 1) and t is headingLabel) then
                if t is not "" then set output to output & t & linefeed
            end if
        end if
    end repeat
end if
if output is "" then error "Assistant heading found, but no reply text exposed. Run --app chatgpt debug-read."
return output
'''

LOCATE_PROMPT = r'''
on readDraft(el)
    with timeout of 2 seconds
        tell application "System Events" to return (value of attribute "AXValue" of el) as text
    end timeout
end readDraft

on refreshPrompt(appProcess)
    with timeout of 2 seconds
        set freshWindow to my mainWindow(appProcess)
    end timeout
    return my locatePrompt(appProcess, freshWindow)
end refreshPrompt

on verifySubmission(target, appProcess)
    set refreshCount to 0
    repeat 30 times
        delay 0.1
        try
            set draft to my readDraft(target)
            if my emptyPrompt(draft, target) then return "Submitted; prompt cleared."
        on error
            if refreshCount >= 2 then error "Return was pressed, but the input keeps changing. Check the conversation before retrying; the message may already be sent."
            log "Input changed after submission; finding the new input..."
            set refreshCount to refreshCount + 1
            try
                set target to my refreshPrompt(appProcess)
            on error
                error "Return was pressed, but confirmation failed. Check the conversation before retrying; the message may already be sent."
            end try
        end try
    end repeat
    error "Return pressed, but prompt did not clear. Check the app before retrying."
end verifySubmission

on emptyPrompt(textValue, el)
    if textValue is "" or textValue is linefeed or textValue is return then return true
    -- Exact empty-editor sentinel observed in the user's AXValue diagnostic.
    -- Do not discard arbitrary text or all values containing the placeholder.
    if textValue is (linefeed & "Do anything") and my attr(el, "AXDescription") is "Do anything" then return true
    return false
end emptyPrompt

on matchesInput(actualText, expectedText)
    -- The editor may expose a structural newline around its value.
    return actualText is expectedText or actualText is (expectedText & linefeed) or actualText is (linefeed & expectedText) or actualText is (linefeed & expectedText & linefeed)
end matchesInput

on isComposer(el)
    with timeout of 1 second
        if my attr(el, "AXRole") is not "AXTextArea" then return false
        return my attr(el, "AXDescription") is "Do anything"
    end timeout
end isComposer

on locatePrompt(appProcess, win)
    -- Search only the selected main window, regardless of focus in other windows.
    set stack to {win}
    set visited to 0
    set started to current date
    repeat while (count stack) > 0 and visited < 1200
        if ((current date) - started) >= 12 then exit repeat
        set el to item -1 of stack
        if (count stack) is 1 then
            set stack to {}
        else
            set stack to items 1 thru -2 of stack
        end if
        set visited to visited + 1
        if visited mod 100 is 0 then log "Looking for composer; checked " & visited & " controls..."
        if my isComposer(el) then return el
        try
            with timeout of 1 second
                tell application "System Events" to set children to UI elements of el
            end timeout
            -- Last children first: composer/footer before long message history.
            repeat with childEl in children
                if (count stack) >= 2000 then exit repeat
                set end of stack to contents of childEl
            end repeat
        end try
    end repeat
    error "Could not find the Do anything input within 12 seconds / 1200 controls. Nothing entered."
end locatePrompt
'''
INPUT_COMMON = LOCATE_PROMPT + COMMON.replace("        set elems to entire contents of win", "        set elems to {}")

INPUT = r'''
log "Locating the Do anything input..."
set target to my locatePrompt(appProcess, win)
log "Found input; checking draft..."
set inputText to system attribute "CTL_ARG"
set commandName to system attribute "CTL_CMD"
with timeout of 2 seconds
    tell application "System Events" to set priorText to value of attribute "AXValue" of target as text
end timeout
if commandName is "type" or commandName is "send" then
    if not my emptyPrompt(priorText, target) then error "Prompt already contains a draft; send or clear it first."
else
    if my emptyPrompt(priorText, target) then error "Prompt is empty; nothing submitted."
end if
tell application "System Events"
    set value of attribute "AXFocused" of target to true
    if (value of attribute "AXFocused" of target) is not true then error "Could not focus prompt."
end tell
if commandName is "type" or commandName is "send" then
    -- Keep the clipboard intact even if input verification fails.
    set oldClipboard to the clipboard as record
    try
        log "Pasting text..."
        set the clipboard to inputText
        tell application "System Events" to keystroke "v" using command down
        set pasted to false
        repeat 30 times
            delay 0.1
            set actualText to my attr(target, "AXValue")
            if my matchesInput(actualText, inputText) then
                set pasted to true
                exit repeat
            end if
        end repeat
        if not pasted then error "Could not verify pasted text; nothing submitted."
    on error errText number errNum
        set the clipboard to oldClipboard
        error errText number errNum
    end try
    set the clipboard to oldClipboard
end if
if commandName is "type" then return "Typed and verified."
tell application "System Events" to key code 36
log "Return pressed; checking submission..."
-- Only reacquire and read after Return; never resend when confirmation fails.
return my verifySubmission(target, appProcess)
'''

INSPECT_INPUT = r'''
set target to my locatePrompt(appProcess, win)
with timeout of 2 seconds
    tell application "System Events" to set draft to value of attribute "AXValue" of target as text
end timeout
return draft
'''

SCRIPTS = {"mode": None, "debug-mode": None, "debug-ui": DEBUG, "debug-focus": FOCUS, "debug-input": INSPECT_INPUT, "debug-read": DEBUG_READ,
           "sessions": None, "session": None, "projects": None, "debug-sidebar": None, "read": READ,
           "type": INPUT, "send": INPUT, "enter": INPUT, "return": INPUT}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print('Usage: ./agent_ctl.py --app chatgpt COMMAND [TEXT]\n'
              'Commands: mode [chatgpt|codex] (chat/code also accepted), debug-mode, debug-ui, debug-focus, debug-input, debug-read, debug-sidebar, projects, sessions,\n'
              '          session "title", read, type "text", send "text", enter, return\n'
              '          sessions --project "GenAx" (expands that project if collapsed)\n'
              '          sessions --recents (only rows under Recents; expands it if collapsed)\n'
              'Projects and session lists are read from the currently selected view.\n'
              'ChatGPT support is experimental; UI matching needs user verification.\n'
              'sessions lists visible sidebar candidates; read returns the latest exposed reply.')
        return 0
    cmd = args[0].lower()
    if cmd not in SCRIPTS:
        print(f'Unknown command: {cmd}', file=sys.stderr)
        return 2
    project_name = ""
    recents_only = False
    if cmd == "sessions" and len(args) > 1 and "--recents" in args[1:]:
        if args[1:] != ["--recents"]:
            print('Use sessions --recents by itself; it cannot be combined with --project or other arguments.', file=sys.stderr)
            return 2
        recents_only = True
        args = [cmd]
    if cmd == "sessions" and len(args) > 1 and args[1] == "--project":
        project_name = " ".join(args[2:]).strip()
        if not project_name:
            print('sessions --project requires a project name.', file=sys.stderr)
            return 2
        args = [cmd]
    arg = " ".join(args[1:])
    if cmd in ("type", "send", "session") and not arg.strip():
        print(f'{cmd} requires non-empty text.', file=sys.stderr)
        return 2
    if cmd == "mode":
        arg = arg.lower()
        if arg == "codex":
            arg = "code"
        if arg == "chatgpt":
            arg = "chat"
        if arg not in ("", "chat", "code"):
            print('ChatGPT mode must be chat or code.', file=sys.stderr)
            return 2
    elif cmd not in ("type", "send", "session") and arg:
        print(f'{cmd} takes no arguments.', file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.update(CTL_CMD=cmd, CTL_ARG=arg, CTL_PROJECT=project_name, CTL_RECENTS="1" if recents_only else "0")
    if cmd in ("debug-ui", "debug-focus"):
        debug_timeout = 30 if cmd == "debug-focus" else 20
        print(f"Inspecting ChatGPT controls ({debug_timeout}-second limit); results print as found...", flush=True)
        try:
            # Inherit terminal streams: AppleScript log output appears immediately.
            result = subprocess.run(['osascript', '-e', DEBUG_COMMON + SCRIPTS[cmd]],
                                    env=env, timeout=debug_timeout)
            return result.returncode
        except subprocess.TimeoutExpired:
            print(f"Diagnostic stopped after {debug_timeout} seconds. Share the partial output above.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Diagnostic cancelled.", file=sys.stderr)
            return 130
    if cmd == "debug-read":
        print("Inspecting the latest reply (30-second limit); output includes short text excerpts...", flush=True)
        try:
            result = subprocess.run(['osascript', '-e', COMMON + DEBUG_READ], env=env, timeout=30)
            return result.returncode
        except subprocess.TimeoutExpired:
            print("Reply inspection stopped after 30 seconds. Share the partial output.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Reply inspection cancelled.", file=sys.stderr)
            return 130
    if cmd == "debug-input":
        print("Reading the input box value; no typing or submission (25-second limit)...", flush=True)
        try:
            result = subprocess.run(['osascript', '-e', INPUT_COMMON + INSPECT_INPUT],
                                    env=env, text=True, capture_output=True, timeout=25)
        except subprocess.TimeoutExpired:
            print("Input inspection timed out.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Input inspection cancelled.", file=sys.stderr)
            return 130
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode:
            return result.returncode
        # osascript appends one output newline; preserve every character in AXValue.
        raw = result.stdout.removesuffix("\n")
        print(f"AXValue length: {len(raw)}")
        print(f"Escaped value (first 160 characters): {ascii(raw[:160])}")
        print("Characters: " + ", ".join(
            f"U+{ord(c):04X} {unicodedata.name(c, 'CONTROL')}" for c in raw[:160]))
        return 0
    if cmd in ("sessions", "session", "projects", "debug-sidebar", "mode", "debug-mode"):
        sidebar_timeout = 75 if project_name or recents_only or cmd == "projects" else 45
        operation = "Inspecting ChatGPT/Codex mode" if cmd in ("mode", "debug-mode") else "Reading ChatGPT sidebar"
        print(f"{operation} ({sidebar_timeout}-second limit)...", file=sys.stderr, flush=True)
        argv, env = helper('chatgpt_sidebar', cmd)
        env.update(CTL_CMD=cmd, CTL_ARG=arg, CTL_PROJECT=project_name, CTL_RECENTS="1" if recents_only else "0")
        try:
            result = subprocess.run(argv, env=env, timeout=sidebar_timeout)
            return result.returncode
        except subprocess.TimeoutExpired:
            print(f"{operation} stopped after {sidebar_timeout} seconds. Check the app before retrying.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Sidebar lookup cancelled.", file=sys.stderr)
            return 130
    if cmd in ("type", "send", "enter", "return"):
        print("Finding ChatGPT input (25-second limit)...", file=sys.stderr, flush=True)
        try:
            result = subprocess.run(['osascript', '-e', INPUT_COMMON + INPUT],
                                    env=env, timeout=25)
            return result.returncode
        except subprocess.TimeoutExpired:
            print("Stopped after 25 seconds. Check the draft before retrying; nothing will be retried automatically.", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Cancelled. Check the draft before retrying.", file=sys.stderr)
            return 130
    try:
        result = subprocess.run(['osascript', '-e', COMMON + SCRIPTS[cmd]],
                                env=env, text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        print('Command timed out after 60 seconds.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Command cancelled.', file=sys.stderr)
        return 130
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode

