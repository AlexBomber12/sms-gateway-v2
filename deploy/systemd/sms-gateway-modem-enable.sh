#!/bin/sh
set -u

MAX_ATTEMPTS=10
RETRY_DELAY_SECONDS=5
MMCLI=/usr/bin/mmcli

find_modem_index() {
    "$MMCLI" -L 2>/dev/null \
        | sed -n 's#.*Modem/\([0-9][0-9]*\).*#\1#p' \
        | head -n 1
}

read_modem_field() {
    field_name="$1"
    sed -n "s/^[[:space:]|]*${field_name}:[[:space:]]*//p" | head -n 1
}

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    modem_index="$(find_modem_index)"
    if [ -z "$modem_index" ]; then
        echo "attempt ${attempt}/${MAX_ATTEMPTS}: no modem visible to ModemManager" >&2
    else
        modem_details="$("$MMCLI" -m "$modem_index" 2>&1)"
        mmcli_status=$?
        if [ "$mmcli_status" -ne 0 ]; then
            echo "attempt ${attempt}/${MAX_ATTEMPTS}: failed to read modem ${modem_index}: ${modem_details}" >&2
        else
            modem_state="$(printf '%s\n' "$modem_details" | read_modem_field "state")"
            power_state="$(printf '%s\n' "$modem_details" | read_modem_field "power state")"

            case "$modem_state" in
                registered|enabled|searching|connecting|connected)
                    echo "modem ${modem_index} already active: state=${modem_state}"
                    exit 0
                    ;;
            esac

            if [ -n "$power_state" ] && [ "$power_state" != "on" ]; then
                echo "modem ${modem_index} power state is ${power_state}; physical power cycle required" >&2
                exit 1
            fi

            if "$MMCLI" -m "$modem_index" --enable; then
                echo "enabled modem ${modem_index}"
                exit 0
            fi
        fi
    fi

    if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
        sleep "$RETRY_DELAY_SECONDS"
    fi
    attempt=$((attempt + 1))
done

echo "modem enable failed after ${MAX_ATTEMPTS} attempts" >&2
exit 1
