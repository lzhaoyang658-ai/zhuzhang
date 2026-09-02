#!/bin/sh

set -eu

readonly database_dir="/mnt/clamav-db"
readonly seed_dir="/var/lib/clamav"
readonly runtime_dir="/tmp/clamav"
readonly scan_dir="${runtime_dir}/scan"
readonly clamd_config="/etc/clamav/vefaas-clamd.conf"
readonly freshclam_config="/etc/clamav/vefaas-freshclam.conf"

clamd_pid=""
freshclam_pid=""

stop_processes() {
    trap - EXIT INT TERM HUP

    if [ -n "$freshclam_pid" ] && kill -0 "$freshclam_pid" 2>/dev/null; then
        kill -TERM "$freshclam_pid" 2>/dev/null || true
    fi
    if [ -n "$clamd_pid" ] && kill -0 "$clamd_pid" 2>/dev/null; then
        kill -TERM "$clamd_pid" 2>/dev/null || true
    fi

    [ -z "$freshclam_pid" ] || wait "$freshclam_pid" 2>/dev/null || true
    [ -z "$clamd_pid" ] || wait "$clamd_pid" 2>/dev/null || true
}

trap stop_processes EXIT INT TERM HUP

mkdir -p "$database_dir" "$runtime_dir" "$scan_dir"
chown -R vefaas-clamav:vefaas-clamav "$database_dir" "$runtime_dir"
chmod 0750 "$database_dir" "$runtime_dir" "$scan_dir"

if [ ! -f "${database_dir}/main.cvd" ] && [ ! -f "${database_dir}/main.cld" ]; then
    cp -a "${seed_dir}/." "${database_dir}/"
    chown -R vefaas-clamav:vefaas-clamav "$database_dir"
fi

clamd --config-file="$clamd_config" --foreground &
clamd_pid="$!"

startup_timeout="${CLAMD_STARTUP_TIMEOUT_SECONDS:-360}"
case "$startup_timeout" in
    ''|*[!0-9]*) echo "CLAMD_STARTUP_TIMEOUT_SECONDS must be a positive integer" >&2; exit 1 ;;
    0) echo "CLAMD_STARTUP_TIMEOUT_SECONDS must be greater than zero" >&2; exit 1 ;;
esac

elapsed=0
until /usr/local/bin/clamav-healthcheck >/dev/null 2>&1; do
    if ! kill -0 "$clamd_pid" 2>/dev/null; then
        wait "$clamd_pid" || true
        echo "clamd exited before becoming ready" >&2
        exit 1
    fi
    if [ "$elapsed" -ge "$startup_timeout" ]; then
        echo "clamd did not become ready within ${startup_timeout} seconds" >&2
        exit 1
    fi
    sleep 1
    elapsed="$((elapsed + 1))"
done

if [ "${CLAMAV_DISABLE_FRESHCLAM:-false}" != "true" ]; then
    freshclam \
        --config-file="$freshclam_config" \
        --checks="${FRESHCLAM_CHECKS:-1}" \
        --daemon \
        --foreground \
        --stdout \
        --user="vefaas-clamav" &
    freshclam_pid="$!"
fi

while :; do
    if ! kill -0 "$clamd_pid" 2>/dev/null; then
        wait "$clamd_pid" || true
        echo "clamd exited unexpectedly" >&2
        exit 1
    fi
    if [ -n "$freshclam_pid" ] && ! kill -0 "$freshclam_pid" 2>/dev/null; then
        wait "$freshclam_pid" || true
        echo "freshclam exited unexpectedly" >&2
        exit 1
    fi
    sleep 5
done
