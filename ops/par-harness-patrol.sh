#!/bin/sh
# Read-only Harness patrol, started by launchd (see docs/OPERATIONS.md, "Periodic patrol").
#
# It runs `par harness check` and nothing else: it never saves a baseline and never touches a Harness
# folder. It writes only two files inside the Runtime data directory (last_patrol.txt / .err) and tells
# you only when something needs attention: the check failed, differs from the baseline, has no
# baseline to compare with, could not run, or the data directory is missing.
set -u
umask 077

DATA="${PAR_DATA_DIR:-$HOME/Library/Application Support/PersonalAgentRuntime}"
PAR="${PAR_BIN:-$HOME/Documents/Personal Agent Runtime/.venv/bin/par}"
NOTIFY="${PAR_NOTIFIER:-/usr/bin/osascript}"
OUT="$DATA/harness"

# Text goes in as arguments, never into the AppleScript source, so nothing printed by the check can
# be interpreted as code.
notify() {
  "$NOTIFY" -e 'on run argv' \
            -e 'display notification (item 1 of argv) with title "Harness 巡检需要关注" subtitle (item 2 of argv)' \
            -e 'end run' "$1" "$2" >/dev/null 2>&1
}

if [ ! -d "$OUT" ]; then
  notify "Runtime data directory is missing" "run: par init"
  exit 0
fi

"$PAR" harness check > "$OUT/last_patrol.txt" 2> "$OUT/last_patrol.err"
rc=$?

reason=""
if [ "$rc" -ne 0 ]; then
  reason="exit status $rc"
elif grep -q '^Baseline: none yet' "$OUT/last_patrol.txt"; then
  reason="no baseline yet"
fi

if [ -n "$reason" ]; then
  summary=$(grep '^Summary:' "$OUT/last_patrol.txt" | head -n 1)
  [ -n "$summary" ] || summary="the check could not run (exit $rc)"
  notify "$summary" "$reason - run: par harness check"
fi
exit 0   # launchd must never see this job as failed
