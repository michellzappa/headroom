#!/usr/bin/env bash
# Update the installed Headroom.app from a GitHub Release.
#
#   ./scripts/update-app.sh            # update if a newer release exists
#   ./scripts/update-app.sh --check    # say what would happen, change nothing
#   ./scripts/update-app.sh --yes      # no confirmation prompt
#   ./scripts/update-app.sh --version 1.2.3
#   ./scripts/update-app.sh --url https://…/Headroom-macOS.zip
#   ./scripts/update-app.sh --force    # reinstall even at the same version
#
# Builds up to 2.3.3 run this too, from inside their own bundle, passing the
# version and URL they read from docs/latest.json. From 2.3.4 the app updates
# with Sparkle and does not bundle this script (docs/updater.md).
#
# Works from a clone or on its own — no gh, no auth, public repo. System tools
# only: curl, unzip, plutil, codesign, spctl, launchctl.
#
# Why this is not just "unzip over the top": the host does not run from the
# clone, it runs from *inside the bundle*
# (Contents/Resources/host/headroom_server.py) under a KeepAlive LaunchAgent.
# Replacing the app under a live agent leaves launchd holding the old code and
# restarting it within seconds, so the swap has to bracket itself with
# bootout/bootstrap. The board's usage data comes through that process, so a
# failed restore looks exactly like broken hardware.
set -euo pipefail

REPO="${HEADROOM_GITHUB_REPO:-michellzappa/headroom}"
APP="${HEADROOM_APP_PATH:-/Applications/Headroom.app}"
LABEL="com.centaur-labs.headroom"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# The only team whose signature may replace the app on this machine. A
# notarized bundle from anyone else is still notarized; it is just not ours.
TEAM_ID="992N457T8D"

CHECK_ONLY=0
ASSUME_YES=0
FORCE=0
WANT_VERSION=""
WANT_URL=""

die() { echo "error: $*" >&2; exit 1; }
note() { echo "$*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check|-n) CHECK_ONLY=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --force) FORCE=1; shift ;;
    --version) WANT_VERSION="${2:-}"; shift 2 ;;
    --url) WANT_URL="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

command -v curl >/dev/null || die "curl required"

installed_version() {
  [[ -d "$APP" ]] || return 0
  plutil -extract CFBundleShortVersionString raw -o - \
    "$APP/Contents/Info.plist" 2>/dev/null || true
}

# Highest of two versions by sort -V, so 1.0.10 beats 1.0.9 the way the
# release history needs it to and a plain string compare would not.
newest() { printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1; }

# ---------------------------------------------------------------- what is out

if [[ -n "$WANT_VERSION" ]]; then
  TAG="v${WANT_VERSION#v}"
else
  # /releases/latest excludes drafts and prereleases, which is what we want:
  # an unfinished tag must never replace a working install.
  TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
  [[ -n "$TAG" ]] || die "could not read the latest release tag from GitHub"
fi
LATEST="${TAG#v}"
INSTALLED="$(installed_version)"

if [[ -z "$INSTALLED" ]]; then
  note "No app at $APP — this will install $LATEST fresh."
else
  note "installed $INSTALLED · latest $LATEST"
fi

if [[ -n "$INSTALLED" && "$FORCE" -eq 0 && -z "$WANT_VERSION" ]]; then
  if [[ "$INSTALLED" == "$LATEST" || "$(newest "$INSTALLED" "$LATEST")" == "$INSTALLED" ]]; then
    note "Already up to date."
    exit 0
  fi
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  note "Would install $LATEST. Nothing changed (--check)."
  exit 0
fi

# Ask before anything happens, not after the download — this is the point where
# the answer still changes what the script does. Default is yes: you ran an
# update command, and the checks below are what stand between a bad download
# and your working install, not this prompt.
if [[ "$ASSUME_YES" -eq 0 ]]; then
  printf 'Update %s (%s → %s)? [Y/n] ' "$APP" "${INSTALLED:-none}" "$LATEST" >&2
  read -r reply </dev/tty || reply=""
  case "$reply" in
    ""|y|Y|yes|YES) ;;
    *) note "Cancelled."; exit 0 ;;
  esac
fi

# ------------------------------------------------------------------- fetch it

STAGE="$(mktemp -d -t headroom-update)"
INSTALLED_OK=0
QUIT_APP=0
# Reopen on any early exit. The app is closed up front so the replace is never
# racing a running copy, which means every path out of here before the install
# owes the user their app back.
cleanup() {
  rm -rf "$STAGE"
  if [[ "$QUIT_APP" -eq 1 && "$INSTALLED_OK" -eq 0 && -d "$APP" ]]; then
    open "$APP" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# Close it now rather than just before the swap. ditto over a running bundle
# replaces files out from under mapped images, and the app that survives that
# is not one you would want to trust.
if pgrep -x Headroom >/dev/null 2>&1; then
  note "Closing Headroom…"
  QUIT_APP=1
  osascript -e 'quit app "Headroom"' >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    pgrep -x Headroom >/dev/null 2>&1 || break
    sleep 0.5
  done
  pgrep -x Headroom >/dev/null 2>&1 && note "note: Headroom did not quit, continuing anyway"
fi

# --url wins so the zip can live anywhere the feed says. The verification
# below is unchanged either way: whatever answers still has to be notarized
# under our team and carry the version its tag claims, so a wrong URL fails
# closed rather than installing something.
ZIP_URL="${WANT_URL:-https://github.com/$REPO/releases/download/$TAG/Headroom-macOS.zip}"
note "Downloading $TAG …"
curl -fsSL --proto '=https' --tlsv1.2 -o "$STAGE/Headroom-macOS.zip" "$ZIP_URL" \
  || die "download failed: $ZIP_URL"
unzip -oq "$STAGE/Headroom-macOS.zip" -d "$STAGE/x" || die "the zip did not unpack"
NEW="$STAGE/x/Headroom.app"
[[ -d "$NEW" ]] || die "no Headroom.app inside the zip"

# ------------------------------------------------------------------ verify it
#
# Everything below runs *before* anything on disk is touched. The app being
# replaced is working software; a download that cannot prove what it is has no
# business overwriting it.

spctl -a -t exec "$NEW" 2>/dev/null \
  || die "Gatekeeper rejected the download — not notarized, refusing to install"

codesign --verify --deep --strict "$NEW" 2>/dev/null \
  || die "signature does not verify — refusing to install"

GOT_TEAM="$(codesign -dv --verbose=4 "$NEW" 2>&1 \
  | sed -n 's/^TeamIdentifier=//p' | head -1)"
[[ "$GOT_TEAM" == "$TEAM_ID" ]] \
  || die "signed by team ${GOT_TEAM:-none}, expected $TEAM_ID — refusing to install"

NEW_VERSION="$(plutil -extract CFBundleShortVersionString raw -o - \
  "$NEW/Contents/Info.plist" 2>/dev/null || true)"
[[ -n "$NEW_VERSION" ]] || die "the downloaded bundle has no version"
if [[ "$NEW_VERSION" != "$LATEST" ]]; then
  die "$TAG contains version $NEW_VERSION — refusing a bundle that disagrees with its tag"
fi

# Not fatal, only reported. A release without these still runs fine; it just
# cannot sync between Macs, and knowing that before installing beats finding
# out from an empty peer list. 1.2.2 shipped carrying three of the five and
# looked correct by every other check.
if codesign -d --entitlements - --xml "$NEW" 2>/dev/null \
    | grep -q "com.apple.developer.icloud-services"; then
  if codesign -d --entitlements - --xml "$NEW" 2>/dev/null \
      | grep -q "com.apple.application-identifier"; then
    note "multi-Mac: iCloud entitlements present"
  else
    note "multi-Mac: WARNING — iCloud keys present but no application identifier;"
    note "           CloudKit cannot open the container in this build"
  fi
else
  note "multi-Mac: this build has no iCloud entitlements, sync will report itself off"
fi

note "Verified $NEW_VERSION, notarized, team $GOT_TEAM."

# ----------------------------------------------------------------- install it

# Stop the agent first. Quitting the app is not enough and neither is kill:
# the host is a KeepAlive LaunchAgent detached from the app (PPID 1), so
# launchd puts it back within seconds and it goes on holding the old bundle's
# files and the board's serial port.
#
# Under the app-owned lifecycle (docs/host.md) there is no job to find, so this
# block does nothing and every launchctl call below stays skipped. The quit
# above is what stopped the host in that mode, and the `open` at the end starts
# a fresh child from the new bundle.
AGENT_WAS_LOADED=0
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  AGENT_WAS_LOADED=1
  note "Stopping the host…"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

# Keep the old bundle until the new one is in place, so a failed copy leaves
# something that launches rather than an empty /Applications entry.
BACKUP=""
if [[ -d "$APP" ]]; then
  BACKUP="$STAGE/Headroom.app.previous"
  mv "$APP" "$BACKUP"
fi

if ! ditto "$NEW" "$APP"; then
  note "copy failed — restoring the previous app"
  rm -rf "$APP"
  [[ -n "$BACKUP" ]] && mv "$BACKUP" "$APP"
  if [[ "$AGENT_WAS_LOADED" -eq 1 && -f "$PLIST" ]]; then
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  fi
  die "install failed, previous version restored"
fi

# Gatekeeper caches by path; strip quarantine so the replaced bundle opens
# without the "downloaded from the internet" prompt it already passed.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
INSTALLED_OK=1

# Always put the agent back. The board reads its usage from that process, so
# leaving it unloaded looks exactly like a dead board on the next boot.
if [[ "$AGENT_WAS_LOADED" -eq 1 ]]; then
  if [[ -f "$PLIST" ]]; then
    note "Starting the host…"
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  else
    note "note: $PLIST is gone — open the app and use Start host"
  fi
fi

open "$APP" 2>/dev/null || true

note "Installed $NEW_VERSION."
note "Settings → Other Macs shows whether multi-Mac came up."
