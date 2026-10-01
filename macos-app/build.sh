#!/bin/bash
# Build m’kay.app (the menu-bar app) and a disk image to distribute it.
#
#   ./build.sh                  build/m’kay.app and build/m’kay-VERSION.dmg
#
# Users see the name m’kay (the app, the disk image and its file); the bundle ID
# (ai.mkay.mac), the executable and the app's folders keep mkay.
#
# The app is the Swift shell (Sources/) with a standalone Python and the public connector
# chain inside: multi-agent-mcp/connector.py -> agent_mcp.py -> multi-agent-cli. Nothing is
# downloaded when users run it.
#
# Environment:
#   MKAY_SIGN_IDENTITY   codesign identity; default: the first "Developer ID Application"
#                        in the keychain, else ad-hoc ("-", for this Mac only)
#   MKAY_NOTARY_PROFILE  notarytool keychain profile; when set, the disk image is notarized
#                        and stapled (xcrun notarytool store-credentials PROFILE ...)
#   MKAY_PYTHON          Python version to bundle (default 3.12)
#
# release.sh publishes the disk image (and the update feed) as a GitHub release.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
BUILD=$HERE/build
NAME=m’kay
APP=$BUILD/$NAME.app
RES=$APP/Contents/Resources
PYTHON_VERSION=${MKAY_PYTHON:-3.12}

[ "$(uname -m)" = arm64 ] || { echo "Build on Apple Silicon (the bundle is arm64 only for now)." >&2; exit 1; }

IDENTITY=${MKAY_SIGN_IDENTITY:-}
if [ -z "$IDENTITY" ]; then
    IDENTITY=$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)
    IDENTITY=${IDENTITY:--}
fi
SIGN=(codesign --force --sign "$IDENTITY")
[ "$IDENTITY" != - ] && SIGN+=(--options runtime --timestamp)

step() { printf '\n== %s\n' "$*"; }

step "Swift shell"
swift build -c release --package-path "$HERE" --arch arm64
BIN_DIR=$(swift build -c release --package-path "$HERE" --arch arm64 --show-bin-path)

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$RES"
cp "$BIN_DIR/mkay" "$APP/Contents/MacOS/mkay"
install_name_tool -add_rpath @executable_path/../Frameworks "$APP/Contents/MacOS/mkay"
# Sparkle (updates). Its XPC services are only for sandboxed apps.
SPARKLE=$APP/Contents/Frameworks/Sparkle.framework
ditto "$BIN_DIR/Sparkle.framework" "$SPARKLE"
rm -rf "$SPARKLE/Versions/B/XPCServices" "$SPARKLE/XPCServices"
cp "$HERE/Resources/Info.plist" "$APP/Contents/Info.plist"
VERSION=$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")
plutil -replace CFBundleVersion -string "$(git -C "$ROOT" rev-list --count HEAD)" "$APP/Contents/Info.plist"

step "Icon"
swift "$HERE/make-icon.swift" "$BUILD/AppIcon.iconset"
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$RES/AppIcon.icns"

step "Python $PYTHON_VERSION (python-build-standalone, via uv)"
uv python install "$PYTHON_VERSION" >/dev/null
PY_HOME=$(dirname "$(dirname "$(realpath "$(uv python find --managed-python "$PYTHON_VERSION")")")")
ditto "$PY_HOME" "$RES/python"
PY=$RES/python/bin/python3
STDLIB=$("$PY" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')
# Tk, IDLE and the headers are not used; EXTERNALLY-MANAGED would refuse the install below.
rm -rf "$RES/python/include" "$RES/python/share" "$RES/python/lib/pkgconfig" \
       "$RES"/python/lib/{itcl,tcl,tk,thread}* "$RES"/python/lib/libtcl* \
       "$STDLIB"/{idlelib,tkinter,turtledemo,ensurepip,EXTERNALLY-MANAGED} \
       "$STDLIB"/lib-dynload/_tkinter*
find "$RES/python" -name __pycache__ -type d -prune -exec rm -rf {} +

step "Dependencies (from the scripts' lock files)"
REQS=$BUILD/requirements.txt
for script in agent_mcp.py connector.py; do
    uv export --quiet --script "$ROOT/multi-agent-mcp/$script" --no-hashes --no-header --no-annotate
done | sort -u > "$REQS"
uv pip install --quiet --python "$PY" --no-cache --no-deps -r "$REQS"

step "Public code"
mkdir -p "$RES/mkay/multi-agent-cli/backends" "$RES/mkay/multi-agent-mcp"
cp "$ROOT"/multi-agent-cli/{agent_ctl.py,ax_native.py} "$RES/mkay/multi-agent-cli/"
cp "$ROOT"/multi-agent-cli/backends/*.py "$RES/mkay/multi-agent-cli/backends/"
cp "$ROOT"/multi-agent-mcp/{agent_mcp.py,connector.py} "$RES/mkay/multi-agent-mcp/"
cp "$ROOT/LICENSE" "$RES/mkay/"

# The signed bundle must never be written to (the app sets PYTHONDONTWRITEBYTECODE), so
# compile everything now; unchecked hashes do not depend on file times.
"$PY" -m compileall -q -f -j0 --invalidation-mode unchecked-hash "$RES/python/lib" "$RES/mkay" >/dev/null

step "Smoke test"
PYTHONNOUSERSITE=1 "$PY" -c 'import mcp, uvicorn, websockets'
PYTHONNOUSERSITE=1 "$PY" "$RES/mkay/multi-agent-mcp/connector.py" --help >/dev/null
PYTHONNOUSERSITE=1 "$PY" "$RES/mkay/multi-agent-cli/agent_ctl.py" apps --json >/dev/null

step "Sign ($IDENTITY)"
find "$RES/python" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 | xargs -0 "${SIGN[@]}"
"${SIGN[@]}" "$SPARKLE/Versions/B/Autoupdate" "$SPARKLE/Versions/B/Updater.app" "$SPARKLE"
"${SIGN[@]}" --entitlements "$HERE/Resources/mkay.entitlements" "$(realpath "$PY")"
"${SIGN[@]}" --entitlements "$HERE/Resources/mkay.entitlements" "$APP"
codesign --verify --deep --strict "$APP"

step "Disk image"
DMG=$BUILD/$NAME-$VERSION.dmg
STAGE=$BUILD/dmg
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$NAME.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "$NAME $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DMG"
rm -rf "$STAGE"
[ "$IDENTITY" != - ] && codesign --force --sign "$IDENTITY" --timestamp "$DMG"

if [ -n "${MKAY_NOTARY_PROFILE:-}" ]; then
    step "Notarize"
    xcrun notarytool submit "$DMG" --keychain-profile "$MKAY_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose "$DMG"
fi

step "Done"
du -sh "$APP" "$DMG"
[ "$IDENTITY" = - ] && echo "Ad-hoc signed: runs on this Mac only. Other Macs need a Developer ID and notarization."
echo "$DMG"
