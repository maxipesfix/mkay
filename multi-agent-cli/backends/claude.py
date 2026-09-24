"""Claude desktop backend: Chat/Cowork and Code views."""
import os
import subprocess
import sys

from backends import helper


PREPARE_SCRIPT = r'''
on mainWindow()
    with timeout of 2 seconds
        tell application "System Events" to tell process "Claude"
            set matches to windows whose name is "Claude"
        end tell
    end timeout
    if (count matches) is not 1 then error "Cannot identify one Claude main window. Close duplicate main windows and retry."
    return item 1 of matches
end mainWindow
tell application "Claude" to activate
tell application "System Events"
    tell process "Claude"
        set frontmost to true
        -- Electron can expose only its outer window until this is enabled.
        set enhanced to false
        try
            set enhanced to value of attribute "AXEnhancedUserInterface" as boolean
        end try
        if not enhanced then set value of attribute "AXEnhancedUserInterface" to true
    end tell
end tell
delay 0.5
'''


def osa(script, **envvars):
    env = os.environ.copy()
    env.update({k: str(v) for k, v in envvars.items()})
    try:
        p = subprocess.run(
            ["osascript", "-e", PREPARE_SCRIPT + script],
            text=True,
            capture_output=True,
            env=env,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        sys.exit("Stopped after 45 seconds. Check Claude before retrying; no action was retried automatically.")
    except KeyboardInterrupt:
        print("Cancelled. Check Claude before retrying.", file=sys.stderr)
        sys.exit(130)
    if p.stderr.strip():
        # AppleScript `log` output goes to stderr.
        print(p.stderr.strip(), file=sys.stderr)
    if p.returncode != 0:
        sys.exit(p.returncode)
    return p.stdout.strip()


GET_ATTR = r'''
on getAttr(el, attrName)
    tell application "System Events"
        try
            return (value of attribute attrName of el) as text
        on error
            return "<missing>"
        end try
    end tell
end getAttr
'''


MODE_HELPERS = GET_ATTR + r'''
on modeName(el)
    if my getAttr(el, "AXRole") is not "AXRadioButton" then return ""
    set d to my getAttr(el, "AXDescription")
    if d starts with "Chat and Cowork" then return "chat"
    if d is "Code" or d starts with "Code," then return "code"
    return ""
end modeName

on currentMode()
    tell application "System Events" to set elems to entire contents of (my mainWindow())
    repeat with elementRef in elems
        set el to contents of elementRef
        set m to my modeName(el)
        if m is not "" then
            try
                tell application "System Events" to set rawValue to value of attribute "AXValue" of el
                if (rawValue as integer) is 1 then return m
            end try
        end if
    end repeat
    return ""
end currentMode
'''


MODE_SCRIPT = MODE_HELPERS + r'''
set wanted to system attribute "CLAUDE_MODE"
if wanted is "cowork" or wanted is "claude" then set wanted to "chat"
set displayName to "Code"
if wanted is "chat" then set displayName to "Chat/Cowork"
if my currentMode() is wanted then return "Already in " & displayName

tell application "System Events" to set elems to entire contents of (my mainWindow())
set target to missing value
repeat with elementRef in elems
    set el to contents of elementRef
    if my modeName(el) is wanted then
        set target to el
        exit repeat
    end if
end repeat
if target is missing value then error "Mode not found: " & wanted
tell application "System Events" to perform action "AXPress" of target
-- A press can replace the AX tree. Read fresh controls to verify, never re-press.
repeat 8 times
    delay 0.25
    if my currentMode() is wanted then return "Switched to " & displayName
end repeat
error "Pressed the mode control, but could not verify " & wanted & ". Run mode to check."
'''


CURRENT_MODE_SCRIPT = MODE_HELPERS + r'''
repeat 3 times
    set resultMode to my currentMode()
    if resultMode is not "" then return resultMode
    delay 0.25
end repeat
error "Could not identify Claude mode."
'''


DEBUG_MODE_SCRIPT = MODE_HELPERS + r'''
set output to ""
set found to 0
tell application "System Events" to set elems to entire contents of (my mainWindow())
repeat with elementRef in elems
    set el to contents of elementRef
    if my modeName(el) is not "" then
        set d to my getAttr(el, "AXDescription")
        set v to my getAttr(el, "AXValue")
        set valueType to "<missing>"
        set normalizedValue to "<unavailable>"
        try
            tell application "System Events" to set rawValue to value of attribute "AXValue" of el
            set valueType to (class of rawValue) as text
            set normalizedValue to (rawValue as integer) as text
        end try
        set output to output & "description=" & d & " | value=" & v & " | type=" & valueType & " | integer=" & normalizedValue & linefeed
        set found to found + 1
        if found is 2 then exit repeat
    end if
end repeat
if output is "" then error "No mode AXRadioButton elements found."
return output
'''


SESSION_SCRIPT = r'''
set needle to system attribute "CLAUDE_SESSION"

tell application "Claude" to activate
delay 0.3

tell application "System Events"
    tell process "Claude"
        set frontmost to true

        set elems to entire contents of (my mainWindow())
        repeat with elementRef in elems
            set el to contents of elementRef
            try
                set elName to name of el

                if elName is not missing value then
                    set elName to elName as text

                    if elName contains needle then
                        try
                            perform action "AXPress" of el
                            return "Opened: " & elName
                        end try

                        -- Sometimes the text itself isn't clickable.
                        set p to el
                        repeat 8 times
                            try
                                set p to parent of p
                                if role of p is "AXButton" then
                                    perform action "AXPress" of p
                                    return "Opened: " & elName
                                end if
                            on error
                                exit repeat
                            end try
                        end repeat
                    end if
                end if
            end try
        end repeat
    end tell
end tell

error "Session not found: " & needle
'''


SESSIONS_SCRIPT = r'''
tell application "Claude" to activate
delay 0.3

set output to ""

tell application "System Events"
    tell process "Claude"
        repeat 3 times
        set output to ""
        set elems to entire contents of (my mainWindow())
        repeat with elementRef in elems
            set el to contents of elementRef
            try
                if (role of el as text) is "AXButton" then
                    set n to name of el
                    if n is not missing value then
                        set n to n as text

                        -- Claude Code session status prefixes seen in the UI.
                        if n starts with "Idle " ¬
                            or n starts with "Unread response " ¬
                            or n contains " · Merged " ¬
                            or n starts with "Something went wrong " then

                            set output to output & n & linefeed
                        end if
                    end if
                end if
            end try
        end repeat
        if output is not "" then return output
        delay 0.5
        end repeat
    end tell
end tell

error "No Claude Code sessions found. Check that Code mode and the sidebar are open, or run debug-sessions."
'''


DEBUG_SESSIONS_SCRIPT = GET_ATTR + r'''
set output to ""
tell application "System Events" to tell process "Claude"
    set elems to entire contents of (my mainWindow())
    repeat with elementRef in elems
        set el to contents of elementRef
        set r to role of el as text
        if r is "AXButton" or r is "AXPopUpButton" then
            set output to output & r & " | title=" & my getAttr(el, "AXTitle") & " | description=" & my getAttr(el, "AXDescription") & linefeed
        end if
    end repeat
end tell
if output is "" then error "Claude exposed no buttons. Check its window and Accessibility permission."
return output
'''


READ_SCRIPT = r'''
tell application "Claude" to activate
delay 0.3

tell application "System Events"
    tell process "Claude"
        set elems to entire contents of (my mainWindow())
        set elemCount to count elems
        set startIndex to 0

        -- Find the LAST "Claude responded:" heading.
        repeat with i from elemCount to 1 by -1
            set el to item i of elems

            try
                if (role of el as text) is "AXHeading" then
                    set n to name of el

                    if n is not missing value then
                        set n to n as text

                        if n starts with "Claude responded:" then
                            set startIndex to i
                            exit repeat
                        end if
                    end if
                end if
            end try
        end repeat

        if startIndex is 0 then
            error "No Claude response found."
        end if

        set responseContainer to value of attribute "AXParent" of item startIndex of elems
        set responseElems to entire contents of responseContainer
        set output to ""
        set previousText to ""
        set pastHeading to false
        repeat with elementRef in responseElems
            set el to contents of elementRef

            try
                set r to role of el as text

                if r is "AXToolbar" then exit repeat
                if r is "AXHeading" then
                    set h to name of el

                    if h is not missing value then
                        set h to h as text

                        if h starts with "Claude responded:" then
                            set pastHeading to true
                        else if h starts with "You said:" then
                            exit repeat
                        end if
                    end if
                end if

                if r is "AXStaticText" and pastHeading then
                    set t to value of el

                    if t is not missing value then
                        set t to t as text

                        if t is not "" and t is not previousText then
                            -- Avoid the accessibility duplicate of the heading.
                            if t does not start with "Claude responded:" then
                                set output to output & t & linefeed
                                set previousText to t
                            end if
                        end if
                    end if
                end if
            end try
        end repeat

        if output is "" then
            -- Fallback: at least return heading text.
            try
                return name of item startIndex of elems as text
            end try
        end if

        return output
    end tell
end tell
'''


PROMPT_HELPERS = GET_ATTR + r'''
on promptOf(elems)
    set matches to {}
    set textAreas to {}
    repeat with elementRef in elems
        set el to contents of elementRef
        if my getAttr(el, "AXRole") is "AXTextArea" then
            set end of textAreas to contents of el
            if my getAttr(el, "AXDescription") is "Prompt" then set end of matches to contents of el
        end if
    end repeat
    if (count matches) is 1 then return item 1 of matches
    -- Older layouts may lack a label. Accept only a unique editor, never the
    -- first/last of several editors, which may include search or message editing.
    if (count matches) is 0 and (count textAreas) is 1 then return item 1 of textAreas
    error "Cannot identify one Claude prompt text area. Close other editors and retry."
end promptOf

on focusPrompt(target)
    tell application "System Events"
        set value of attribute "AXFocused" of target to true
        if (value of attribute "AXFocused" of target) is not true then error "Could not focus Claude prompt."
    end tell
end focusPrompt
'''


INPUT_SCRIPT = PROMPT_HELPERS + r'''
set inputText to system attribute "CLAUDE_TEXT"
set shouldSend to system attribute "CLAUDE_SEND"

tell application "Claude" to activate
delay 0.25

tell application "System Events"
    tell process "Claude"
        set frontmost to true
        set elems to entire contents of (my mainWindow())
        set target to my promptOf(elems)

        set priorText to (value of target) as text
        if priorText is not "" and priorText is not linefeed and priorText is not return then error "Prompt already contains a draft; send or clear it first."

        my focusPrompt(target)

        -- Clipboard paste is much more reliable than AppleScript keystroke
        -- for long text, punctuation, unicode, and multiline prompts.
        set oldClipboard to missing value
        try
            set oldClipboard to the clipboard as record
        end try

        try
            set the clipboard to inputText
            keystroke "v" using command down
            set pasted to false
            repeat 20 times
                delay 0.1
                set actualText to value of target as text
                if actualText is inputText or actualText is (inputText & linefeed) then
                    set pasted to true
                    exit repeat
                end if
            end repeat
            if not pasted then error "Could not verify text in Claude prompt; nothing submitted."
            if shouldSend is "1" then key code 36
            delay 0.2
        on error errText number errNum
            if oldClipboard is not missing value then set the clipboard to oldClipboard
            error errText number errNum
        end try
        if oldClipboard is not missing value then set the clipboard to oldClipboard

        if shouldSend is "1" then
            return "Sent."
        else
            return "Typed."
        end if
    end tell
end tell
'''


RETURN_SCRIPT = PROMPT_HELPERS + r'''
tell application "Claude" to activate
delay 0.15

tell application "System Events"
    tell process "Claude"
        set frontmost to true
        set elems to entire contents of (my mainWindow())
        set target to my promptOf(elems)
        set draft to value of target as text
        if draft is "" or draft is linefeed or draft is return then error "Prompt is empty; nothing submitted."
        my focusPrompt(target)
        key code 36
    end tell
end tell

return "Return pressed."
'''


def usage():
    print(
        """Usage (Claude is the default app; see ./agent_ctl.py --help for others):
  ./agent_ctl.py mode code
  ./agent_ctl.py mode chat
  ./agent_ctl.py mode
  ./agent_ctl.py debug-mode
  ./agent_ctl.py projects
  ./agent_ctl.py sessions
  ./agent_ctl.py sessions --project "alltalk"
  ./agent_ctl.py sessions --recents
  ./agent_ctl.py debug-sidebar
  (Chat projects use the Projects page; Code projects are folders.)
  (Chat Recents follows Chats and tasks; Code Recents lists No folder.)
  ./agent_ctl.py debug-sessions
  ./agent_ctl.py session "Nexor-Pipecat parallel calls"
  ./agent_ctl.py read
  ./agent_ctl.py type "check the latest logs"
  ./agent_ctl.py send "check the latest logs"
  ./agent_ctl.py enter

Examples:
  ./agent_ctl.py mode code
  ./agent_ctl.py session "VRM viseme"
  ./agent_ctl.py read
  ./agent_ctl.py send "Run the tests and tell me what fails."
"""
    )


def main(args=None):
    argv = ["claude"] + list(sys.argv[1:] if args is None else args)
    if len(argv) < 2:
        usage()
        sys.exit(1)

    cmd = argv[1].lower()

    if cmd == "mode":
        if len(argv) == 2:
            print(osa(CURRENT_MODE_SCRIPT))
        else:
            mode = argv[2].lower()
            if mode not in ("code", "chat", "cowork", "claude"):
                sys.exit("mode must be code, chat, cowork, or claude")
            print(osa(MODE_SCRIPT, CLAUDE_MODE=mode))

    elif cmd == "debug-mode":
        print(osa(DEBUG_MODE_SCRIPT))

    elif cmd == "debug-sessions":
        print(osa(DEBUG_SESSIONS_SCRIPT))

    elif cmd in ("projects", "sessions", "debug-sidebar"):
        # Native references survive sidebar rerenders without indexed paths.
        command, env = helper('claude_sidebar', *argv[1:])
        try:
            return subprocess.run(command, env=env, timeout=75).returncode
        except subprocess.TimeoutExpired:
            sys.exit('Claude listing stopped after 75 seconds; no action was retried.')
        except KeyboardInterrupt:
            sys.exit(130)

    elif cmd == "session":
        if len(argv) < 3:
            sys.exit('Usage: ./agent_ctl.py session "part of session title"')
        query = " ".join(argv[2:])
        print(osa(SESSION_SCRIPT, CLAUDE_SESSION=query))

    elif cmd == "read":
        print(osa(READ_SCRIPT))

    elif cmd in ("type", "send"):
        if len(argv) < 3:
            sys.exit(f'Usage: ./agent_ctl.py {cmd} "text"')
        message = " ".join(argv[2:])
        print(
            osa(
                INPUT_SCRIPT,
                CLAUDE_TEXT=message,
                CLAUDE_SEND="1" if cmd == "send" else "0",
            )
        )

    elif cmd in ("enter", "return"):
        print(osa(RETURN_SCRIPT))

    else:
        usage()
        sys.exit(1)

    return 0
