# macos-app

`m’kay.app`, a menu-bar app that links this Mac to a voice server (by default
[app.mkay.ai](https://app.mkay.ai)), so you can talk to the Claude, ChatGPT/Codex and
Cursor apps on it from a phone or any browser. It is the easy install: one disk image,
no terminal, no uv, no Python to set up.

The app is a small Swift shell around the public code, unchanged: it runs
`multi-agent-mcp/connector.py` with a bundled standalone Python, and the connector runs
`agent_mcp.py` and the CLI as before. Accessibility and Automation are granted to the
app; its child processes inherit them.

```text
m’kay.app (Swift, menu bar) ──runs──▶ python connector.py --events --python … ──▶ agent_mcp.py ──▶ agent_ctl.py
          ◀── JSON lines: code, connected, tool, disconnected, stopped ──┘
```

## Layout

```text
Package.swift            SwiftPM package (no Xcode project; the Command Line Tools build it)
Sources/mkay/
  main.swift             Menu-bar app (LSUIElement)
  AppDelegate.swift      Status item and its menu
  SetupView.swift        Setup window: Accessibility, Automation, sign-in
  Connector.swift        Starts, stops and restarts connector.py; reads its --events
  Permissions.swift      Accessibility and Automation checks, login item
Resources/Info.plist     Bundle ai.mkay.mac, version, NSAppleEventsUsageDescription
Resources/mkay.entitlements  Apple Events (hardened runtime)
make-icon.swift          Draws the app icon
build.sh                 Builds build/m’kay.app and build/m’kay-VERSION.dmg
```

## Using it

1. Open the disk image and drag m’kay to Applications; open it from there.
2. The setup window asks for **Accessibility** (reading and operating the agent apps)
   and **Automation** of System Events (paste and Return), then **Sign in**: your
   browser opens your account's Link-a-Mac page with a code, and you link the Mac only
   if the page shows the same code as the window.
3. The menu-bar icon (a waveform) shows the connection. Its menu has Open mkay.ai
   (the voice page, in your browser; the setup window has the same button), Recent
   Activity (tool names, newest first), Read-Only (Never Send), Pause, Set Up…, Start at Login, Open Log, Sign Out, About and Quit. Following Apple's
   guidelines for menu-bar menus, items do not repeat the app's name, and only items that
   ask for more (Set Up…, Sign In…) end in an ellipsis.

- The device token is kept in `~/Library/Application Support/mkay/connector-token`
  (mode 600), separate from a connector run in a terminal (`~/.config/agent-mcp`).
  Sign Out forgets it here; Unlink on the account page revokes it on the server.
- Read-Only restarts the connector with `--read-only`: submitting tools are refused on
  this Mac, whatever the server asks.
- The log is `~/Library/Logs/mkay/connector.log`. It has tool names but never their
  arguments, which can hold message text.
- If the server unlinks the Mac or refuses its token, the app forgets the token and asks
  you to sign in again. If the connector crashes, the app starts it again (after 4 s,
  then longer, up to a minute).
- One copy runs at a time (two would take the account's connection from each other,
  dropping every voice session). Opening m’kay while it runs, from anywhere, shows the
  running copy's setup window and the new copy quits. The exception is a copy running
  from the disk image, which never connects (its permissions would be lost): opening
  the one in Applications replaces it. If another Mac or
  terminal connector of the same account takes over, the app pauses; Resume takes it back.
- Development server: `defaults write ai.mkay.mac server http://localhost:7870`
  (`defaults delete ai.mkay.mac server` to go back to app.mkay.ai).

## Building

On Apple Silicon with the Command Line Tools and uv:

```bash
macos-app/build.sh
```

It compiles the Swift shell, copies uv's standalone CPython 3.12 into the bundle (without
Tk, IDLE and headers), installs the exact versions from `agent_mcp.py.lock` and
`connector.py.lock` into it, copies the public code, precompiles everything (the app
never writes into its bundle), smoke-tests the bundled Python, signs every binary
inside-out and makes a disk image with an Applications link. About 110 MB installed, 43 MB
to download.

Signing: `MKAY_SIGN_IDENTITY`, by default the first "Developer ID Application"
certificate in the keychain, else ad-hoc (runs only on the Mac that built it). With a
Developer ID the binaries get the hardened runtime and a timestamp; set
`MKAY_NOTARY_PROFILE` to notarize and staple the disk image:

```bash
xcrun notarytool store-credentials mkay-notary --apple-id YOU@EXAMPLE.COM --team-id TEAMID
MKAY_NOTARY_PROFILE=mkay-notary macos-app/build.sh
```

Permissions are tied to the app's signature: an ad-hoc build loses Accessibility on every
rebuild; Developer ID builds keep it. After switching between the two, System Settings may
show mkay as allowed while the setup window has no checkmark (the entry remembers the
other signature): reset it with `tccutil reset Accessibility ai.mkay.mac`, reopen the app
and allow it again.

## Status

Built 2026-09-27 (ad-hoc signed) and checked on this Mac: the bundled Python serves all 17
MCP tools over stdio, the signature stays valid after running, the setup window shows the
three steps, and the connector's events for a refused token, an unreachable server,
reconnecting and Ctrl-C.

Notarized 2026-09-27: a Developer ID build (hardened runtime) was accepted by Apple and
stapled (the first submission took about 20 minutes); the bundled Python still loads its
native modules and serves the 17 tools under the hardened runtime.

Not yet done: sign-in and a voice session through the app; a first launch on another Mac; updates (Sparkle);
Intel Macs; the token in the Keychain instead of a file.
