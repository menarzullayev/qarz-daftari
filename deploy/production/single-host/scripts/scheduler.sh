#!/usr/bin/env bash
# The backup schedule of the single host, in a container: Windows with Docker Desktop has no systemd
# timers, and the schedule must not depend on anything installed on the host.
#
#   scheduler.sh              run for ever (the `backup` service)
#   scheduler.sh slots [NOW]  print the latest scheduled moment of every job as of NOW (an epoch;
#                             default: now) and exit. Used by the proof to test the calendar.
#
# The calendar is the one of deploy/backup/systemd (DEC-060), in Tashkent time (TZ of the image):
#
#   full           Sunday 01:30               backup.sh full
#   diff           Monday to Saturday 01:30   backup.sh diff
#   restore-test   Wednesday 02:30            restore the latest backup into a throwaway instance, check it
#   expire         daily 03:30                expiry in the repository; pruning of the monthly dumps
#   monthly        1st of the month 04:30     the monthly dump and its copy in the bucket
#   check          every QD_CHECK_INTERVAL s  ages of the newest backup and WAL segment (default 300)
#   heartbeat      every minute               one WAL record, so an idle database still archives
#
# A machine that is started by hand is sometimes off at 01:30. So a job is not "run at its time" but
# "run when its latest scheduled moment has passed and it has not run since": after a night without
# power the missed backup is taken as soon as the machine is up, once, and at first start everything
# is due, so the first full backup and the first restore test happen without anybody asking.
# A job that fails is tried again after QD_RETRY_SECONDS (600).
#
# Every job writes its own JSON line; this script adds one line a job with the exit code. Nothing is
# sent anywhere: the lines are the container's log, the figures are files under $QD_TEXTFILE_DIR.
set -uo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

: "${QD_CHECK_INTERVAL:=300}"
: "${QD_HEARTBEAT_INTERVAL:=60}"
: "${QD_RETRY_SECONDS:=600}"
: "${QD_TICK_SECONDS:=15}"
MARKS="$QD_STATE_DIR/scheduler"

# --- the calendar -------------------------------------------------------------------------------------
# weekly_slot NOW "<days of the week, 0 = Sunday>" HH:MM -> epoch of the latest such moment <= NOW
weekly_slot() {
    local at="$1" days=" $2 " hhmm="$3" back day moment
    for back in 0 1 2 3 4 5 6 7; do
        day="$(date -d "@$((at - back * 86400))" +%F)"
        moment="$(date -d "$day $hhmm" +%s)"
        [ "$moment" -le "$at" ] || continue
        case "$days" in *" $(date -d "@$moment" +%w) "*)
            echo "$moment"
            return 0
            ;;
        esac
    done
    echo 0
}
# monthly_slot NOW HH:MM -> epoch of the latest "1st of a month at HH:MM" <= NOW
monthly_slot() {
    local at="$1" hhmm="$2" first moment
    first="$(date -d "@$at" +%Y-%m-01)"
    moment="$(date -d "$first $hhmm" +%s)"
    if [ "$moment" -gt "$at" ]; then
        moment="$(date -d "$(date -d "$first -1 month" +%F) $hhmm" +%s)"
    fi
    echo "$moment"
}
# slot_of JOB NOW
slot_of() {
    case "$1" in
    full) weekly_slot "$2" "0" 01:30 ;;
    diff) weekly_slot "$2" "1 2 3 4 5 6" 01:30 ;;
    restore-test) weekly_slot "$2" "3" 02:30 ;;
    expire) weekly_slot "$2" "0 1 2 3 4 5 6" 03:30 ;;
    monthly) monthly_slot "$2" 04:30 ;;
    esac
}
CALENDAR_JOBS="full diff restore-test expire monthly"

if [ "${1:-}" = "slots" ]; then
    at="${2:-$(date +%s)}"
    for job in $CALENDAR_JOBS; do
        moment="$(slot_of "$job" "$at")"
        printf '%s %s %s\n' "$job" "$moment" "$(date -d "@$moment" '+%a %F %H:%M %Z')"
    done
    exit 0
fi

# --- running ------------------------------------------------------------------------------------------
say() { printf '{"event":"scheduler","message":"%s","at":"%s"}\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; }
mark() { cat "$MARKS/$1" 2> /dev/null || echo 0; }
set_mark() { printf '%s\n' "$2" > "$MARKS/$1"; }

# run JOB -> the job's exit code. One line of our own, after the job's.
run() {
    local job="$1" code=0 started
    started="$(date +%s)"
    /opt/qarz-single/job.sh "$job" || code=$?
    printf '{"event":"scheduler_job","job":"%s","outcome":"%s","exit_code":%s,"duration_seconds":%s,"at":"%s"}\n' \
        "$job" "$([ "$code" = 0 ] && echo ok || echo failed)" "$code" "$(($(date +%s) - started))" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    return "$code"
}

mkdir -p "$MARKS" "$QD_TEXTFILE_DIR" "$QD_RESTORE_TEST_DIR" "$QD_MONTHLY_DIR"
say "started; calendar in $(date +%Z)"
trap 'say "stopping"; exit 0' TERM INT

stanza_ready=0
while :; do
    at="$(date +%s)"

    # Nothing can be archived or backed up before the stanza exists; the database may still be starting.
    if [ "$stanza_ready" = 0 ]; then
        if [ $((at - $(mark stanza.tried))) -ge 30 ]; then
            set_mark stanza.tried "$at"
            if run stanza; then stanza_ready=1; fi
        fi
    fi

    if [ "$stanza_ready" = 1 ]; then
        if [ $((at - $(mark heartbeat.ran))) -ge "$QD_HEARTBEAT_INTERVAL" ]; then
            set_mark heartbeat.ran "$at"
            /opt/qarz-single/job.sh heartbeat || say "heartbeat failed"
        fi

        for job in $CALENDAR_JOBS; do
            slot="$(slot_of "$job" "$at")"
            [ "$(mark "$job.done")" -lt "$slot" ] || continue
            [ $((at - $(mark "$job.failed"))) -ge "$QD_RETRY_SECONDS" ] || continue
            if run "$job"; then
                set_mark "$job.done" "$(date +%s)"
                # A full backup taken now is also today's backup: no differential on top of it.
                [ "$job" != full ] || set_mark diff.done "$(date +%s)"
            else
                set_mark "$job.failed" "$(date +%s)"
            fi
        done

        if [ $(($(date +%s) - $(mark check.ran))) -ge "$QD_CHECK_INTERVAL" ]; then
            set_mark check.ran "$(date +%s)"
            # Quiet when all is fresh; what is stale or missing is printed, and then our own line too.
            code=0
            /opt/qarz-single/job.sh check --quiet || code=$?
            if [ "$code" != 0 ]; then
                printf '{"event":"scheduler_job","job":"check","outcome":"failed","exit_code":%s,"at":"%s"}\n' \
                    "$code" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            fi
        fi
    fi

    sleep "$QD_TICK_SECONDS" &
    wait $!
done
