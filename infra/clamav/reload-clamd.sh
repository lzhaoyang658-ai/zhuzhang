#!/bin/sh

set -eu

pid_file="/tmp/clamav/clamd.pid"

if [ ! -r "$pid_file" ]; then
    exit 0
fi

clamd_pid="$(cat "$pid_file")"
case "$clamd_pid" in
    ''|*[!0-9]*) exit 1 ;;
esac

if kill -0 "$clamd_pid" 2>/dev/null; then
    kill -USR2 "$clamd_pid"
fi
