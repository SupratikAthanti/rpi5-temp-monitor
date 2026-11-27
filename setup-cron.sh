#!/usr/bin/env bash
# PiStats cron installer -- counterpart to ./stop.sh (design.md §13).
#
# Usage: setup-cron.sh start|stop|status
#
# Only PiStats's collector line is ever added or removed; unrelated crontab
# entries are preserved verbatim.  The crontab executable is resolved from
# CRON_BIN / CRONTAB / CRON_CMD (in that order) and falls back to `crontab` on
# PATH, so tests can inject a fake via PATH without ever touching the real
# crontab.

set -u

CRON_BIN="${CRON_BIN:-${CRONTAB:-${CRON_CMD:-crontab}}}"
ACTION="${1:-status}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECT_MARK='collect.sh'
CRON_LINE="* * * * * ${SCRIPT_DIR}/collect.sh --mode both ${SCRIPT_DIR}/temp_log.csv"

current_crontab() {
    "$CRON_BIN" -l 2>/dev/null || true
}

case "$ACTION" in
    start)
        current="$(current_crontab)"
        if printf '%s\n' "$current" | grep -q -- "$COLLECT_MARK"; then
            printf 'PiStats cron already installed (no changes).\n'
        else
            printf '%s\n%s\n' "$current" "$CRON_LINE" | "$CRON_BIN" -
            printf 'PiStats cron installed: %s\n' "$CRON_LINE"
        fi
        ;;
    stop)
        current="$(current_crontab)"
        printf '%s\n' "$current" | grep -v -- "$COLLECT_MARK" | "$CRON_BIN" -
        printf 'PiStats cron entry removed.\n'
        ;;
    status)
        current="$(current_crontab)"
        if printf '%s\n' "$current" | grep -q -- "$COLLECT_MARK"; then
            printf 'PiStats cron: RUNNING (background collector installed)\n'
        else
            printf 'PiStats cron: STOPPED (no background collector)\n'
        fi
        ;;
    *)
        printf 'Usage: %s start|stop|status\n' "$0" >&2
        exit 2
        ;;
esac

exit 0
