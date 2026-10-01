#!/bin/bash
# Publish the built m’kay as a GitHub release, after MKAY_NOTARY_PROFILE=... ./build.sh:
#
#   ./release.sh "What changed, in a sentence or two."
#
# The release carries two files, under names that must not change:
#   mkay.dmg      the notarized disk image; the landing page links to
#                 releases/latest/download/mkay.dmg
#   appcast.xml   the update feed installed copies check daily (SUFeedURL in Info.plist):
#                 this version, its notes and the disk image's EdDSA signature
#
# The EdDSA private key is in this Mac's login keychain (account ai.mkay.mac), made once with
# Sparkle's generate_keys; its public half is SUPublicEDKey in Info.plist. Without that key
# no installed copy accepts an update: keep a backup (generate_keys --account ai.mkay.mac -x FILE).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
BUILD=$HERE/build
APP=$BUILD/m’kay.app
REPO=maxipesfix/mkay
KEY_ACCOUNT=ai.mkay.mac

NOTES=${1:?usage: release.sh \"What changed.\"}
VERSION=$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist")
BUILD_NUMBER=$(plutil -extract CFBundleVersion raw "$APP/Contents/Info.plist")
MIN_SYSTEM=$(plutil -extract LSMinimumSystemVersion raw "$APP/Contents/Info.plist")
DMG=$BUILD/m’kay-$VERSION.dmg
TAG=v$VERSION

fail() { echo "$*" >&2; exit 1; }
[ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "Commit your changes first."
[ "$BUILD_NUMBER" = "$(git -C "$ROOT" rev-list --count HEAD)" ] || fail "The build is not from this commit: run build.sh again."
[ -n "$(git -C "$ROOT" branch -r --contains HEAD)" ] || fail "Push this commit first."
git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null && fail "$TAG exists: raise the version in Resources/Info.plist."
xcrun stapler validate -q "$DMG" || fail "$DMG is not notarized: build with MKAY_NOTARY_PROFILE set."

# Sparkle's signing tool, the same version as the framework in the app (Package.resolved).
SPARKLE_VERSION=$(plutil -extract pins.0.state.version raw "$HERE/Package.resolved")
TOOLS=$BUILD/sparkle-$SPARKLE_VERSION
if [ ! -x "$TOOLS/bin/sign_update" ]; then
    mkdir -p "$TOOLS"
    curl -fsSL "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz" \
        | tar xJ -C "$TOOLS" ./bin
fi
SIGNATURE=$("$TOOLS/bin/sign_update" --account "$KEY_ACCOUNT" "$DMG")

OUT=$BUILD/release-$VERSION
rm -rf "$OUT" && mkdir -p "$OUT"
cp "$DMG" "$OUT/mkay.dmg"
NOTES_HTML=$(printf '%s' "$NOTES" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')
cat > "$OUT/appcast.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>m’kay</title>
    <item>
      <title>m’kay $VERSION</title>
      <pubDate>$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S +0000')</pubDate>
      <sparkle:version>$BUILD_NUMBER</sparkle:version>
      <sparkle:shortVersionString>$VERSION</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>$MIN_SYSTEM</sparkle:minimumSystemVersion>
      <description><![CDATA[<p>$NOTES_HTML</p>]]></description>
      <enclosure url="https://github.com/$REPO/releases/download/$TAG/mkay.dmg" type="application/octet-stream" $SIGNATURE/>
    </item>
  </channel>
</rss>
EOF
xmllint --noout "$OUT/appcast.xml"

git -C "$ROOT" tag "$TAG"
git -C "$ROOT" push origin "$TAG"
gh release create "$TAG" --repo "$REPO" --verify-tag --title "m’kay $VERSION" --notes "$NOTES" \
    "$OUT/mkay.dmg#m’kay $VERSION for Mac (Apple Silicon)" "$OUT/appcast.xml#Update feed"
