#!/bin/sh
# Native Thingino JPEG spool. No network dependency, no writes outside its SD namespace.
umask 077
SERRA_RUN=${SERRA_RUN_ROOT:-/run}
SERRA_CONFIG=${SERRA_SPOOL_CONFIG:-/etc/serra-spool.conf}
SERRA_STATUS=$SERRA_RUN/serra-spool.status
SERRA_CLOCK=$SERRA_RUN/serra-spool.clock
SERRA_USAGE=$SERRA_RUN/serra-spool.usage

config() {
    . "$SERRA_CONFIG" || return 1
    case "$SERRA_ENABLED:$SERRA_INTERVAL:$SERRA_ROTATION:$SERRA_QUOTA_KB" in *[!0-9:]*|:*) return 1;; esac
}
mounted() {
    mountpoint -q "$SERRA_MOUNT" && [ ! -L "$SERRA_ROOT" ] &&
        [ -f "$SERRA_ROOT/.identity" ] && [ "$(cat "$SERRA_ROOT/.identity")" = "$SERRA_IDENTITY" ]
}
lock() { exec 9>"$SERRA_RUN/serra-spool.media.lock"; flock -x 9; }
unlock() { flock -u 9; }
problem() { printf '%s\n' "$1" > "$SERRA_STATUS"; }
valid_name() {
    printf '%s\n' "$1" | grep -Eq '^[0-9]{10}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-r(0|90|180|270)-t[rbh]\.jpg$'
}
paths() {
    valid_name "$1" || return 1
    SERRA_TS=${1%%-*}
    SERRA_DIR=$SERRA_ROOT/pending/$((SERRA_TS / 3600))
    SERRA_FILE=$SERRA_DIR/$1
    # This legacy exFAT driver aliases appended extensions (a.jpg.meta -> a.jpg).
    # Distinct prefixes keep staging and metadata independent of the JPEG name.
    SERRA_PART=$SERRA_DIR/p-$1
    SERRA_META=$SERRA_DIR/m-${1%.jpg}.txt
    SERRA_META_PART=$SERRA_DIR/w-${1%.jpg}.txt
    [ ! -L "$SERRA_DIR" ] && [ ! -L "$SERRA_FILE" ] && [ ! -L "$SERRA_META" ] && [ ! -L "$SERRA_PART" ] && [ ! -L "$SERRA_META_PART" ]
}
usage_read() {
    SERRA_USED=0; SERRA_COUNT=0
    if [ -f "$SERRA_USAGE" ]; then
        read -r SERRA_USED SERRA_COUNT < "$SERRA_USAGE"
    else
        SERRA_USED=$(du -sk "$SERRA_ROOT/pending" | awk '{print $1}')
        SERRA_COUNT=$(find "$SERRA_ROOT/pending" -name 'm-*.txt' -type f | wc -l)
    fi
    case "$SERRA_USED:$SERRA_COUNT" in *[!0-9:]*|:*) return 1;; esac
}
usage_write() { printf '%s %s\n' "$SERRA_USED" "$SERRA_COUNT" > "$SERRA_USAGE"; }
jpeg_ok() {
    [ "$(head -c 2 "$1" | base64)" = '/9g=' ] &&
        [ "$(tail -c 2 "$1" | base64)" = '/9k=' ]
}
publish_meta() {
    SERRA_SIZE=$(stat -c '%s' "$SERRA_FILE")
    [ "$SERRA_SIZE" -gt 4 ] && [ "$SERRA_SIZE" -le 8388608 ] && jpeg_ok "$SERRA_FILE" || return 1
    SERRA_HASH=$(sha256sum "$SERRA_FILE"); SERRA_HASH=${SERRA_HASH%% *}
    printf '%s %s 0\n' "$SERRA_HASH" "$SERRA_SIZE" > "$SERRA_META_PART" || return 1
    SERRA_COST=$(stat -c '%b' "$SERRA_FILE" "$SERRA_META_PART" | awk '{s+=$1} END {print int((s+1)/2)}')
    printf '%s %s %s\n' "$SERRA_HASH" "$SERRA_SIZE" "$SERRA_COST" > "$SERRA_META_PART" &&
        mv "$SERRA_META_PART" "$SERRA_META" && [ "$(stat -c '%s' "$SERRA_FILE")" = "$SERRA_SIZE" ]
}
recover() {
    mounted || return 1
    lock
    for SERRA_FOLDER in "$SERRA_ROOT"/pending/[0-9]*; do
        [ -d "$SERRA_FOLDER" ] && [ ! -L "$SERRA_FOLDER" ] || continue
        for SERRA_ORPHAN in "$SERRA_FOLDER"/p-*.jpg "$SERRA_FOLDER"/*.jpg; do
            [ -f "$SERRA_ORPHAN" ] && [ ! -L "$SERRA_ORPHAN" ] || continue
            SERRA_NAME=${SERRA_ORPHAN##*/}; SERRA_NAME=${SERRA_NAME#p-}
            paths "$SERRA_NAME" || continue
            [ ! -f "$SERRA_META" ] || continue
            jpeg_ok "$SERRA_ORPHAN" || continue
            [ "$SERRA_ORPHAN" = "$SERRA_FILE" ] || mv "$SERRA_ORPHAN" "$SERRA_FILE"
            publish_meta || problem recovery_error
        done
    done
    rm -f "$SERRA_USAGE"
    usage_read && usage_write
    unlock
}
snapshot() {
    config && mounted || { problem sd_unavailable; return 1; }
    lock
    SERRA_NOW=$(date +%s)
    SERRA_LAST=0; SERRA_HOUR=0; SERRA_ANGLE=0; SERRA_PREVIOUS=''; SERRA_CHANGED=$SERRA_NOW
    [ ! -f "$SERRA_CLOCK" ] || read -r SERRA_LAST SERRA_HOUR SERRA_ANGLE SERRA_PREVIOUS SERRA_CHANGED < "$SERRA_CLOCK"
    if [ ! -f "$SERRA_CLOCK" ] && [ -f "$SERRA_ROOT/.history-slot" ]; then
        read -r SERRA_HOUR SERRA_ANGLE < "$SERRA_ROOT/.history-slot"
    fi
    SERRA_RECENT=0; SERRA_HISTORY=0
    [ "$1" = force ] || [ "$SERRA_ENABLED" = 1 ] || { unlock; return 0; }
    [ "$1" != force ] && [ "$SERRA_NOW" -lt $((SERRA_LAST + SERRA_INTERVAL)) ] && [ "$SERRA_ANGLE" = "$SERRA_ROTATION" ] || SERRA_RECENT=1
    [ "$SERRA_HOUR:$SERRA_ANGLE" = "$((SERRA_NOW / 3600)):$SERRA_ROTATION" ] || SERRA_HISTORY=1
    [ "$SERRA_RECENT:$SERRA_HISTORY" != 0:0 ] || { unlock; return 0; }
    usage_read || { problem usage_error; unlock; return 1; }
    # Reserve room for the largest JPEG plus filesystem metadata; never evict unacknowledged photos.
    SERRA_FREE=$(df -Pk "$SERRA_MOUNT" | awk 'NR==2 {print $4}')
    if [ $((SERRA_USED + 8256)) -gt "$SERRA_QUOTA_KB" ] || [ "${SERRA_FREE:-0}" -lt 139264 ]; then
        problem queue_full; unlock; return 1
    fi
    SERRA_RAM=$SERRA_RUN/serra-spool-image.jpg
    if ! timeout 12 prudyntctl snapshot -c 0 -q 75 > "$SERRA_RAM" || ! jpeg_ok "$SERRA_RAM"; then
        problem snapshot_error; rm -f "$SERRA_RAM"; unlock; return 1
    fi
    SERRA_HASH=$(sha256sum "$SERRA_RAM"); SERRA_HASH=${SERRA_HASH%% *}
    SERRA_TOLERANCE=$((2 * SERRA_INTERVAL)); [ "$SERRA_TOLERANCE" -ge 15 ] || SERRA_TOLERANCE=15
    if [ "$SERRA_HASH" = "$SERRA_PREVIOUS" ] && [ $((SERRA_NOW - SERRA_CHANGED)) -gt "$SERRA_TOLERANCE" ]; then
        problem frozen_snapshot; rm -f "$SERRA_RAM"; unlock; return 1
    fi
    [ "$SERRA_HASH" = "$SERRA_PREVIOUS" ] || SERRA_CHANGED=$SERRA_NOW
    SERRA_TIER=r
    [ "$SERRA_HISTORY" = 0 ] || { SERRA_TIER=h; [ "$SERRA_RECENT" = 0 ] || SERRA_TIER=b; }
    SERRA_NAME=$SERRA_NOW-$(cat /proc/sys/kernel/random/uuid)-r$SERRA_ROTATION-t$SERRA_TIER.jpg
    paths "$SERRA_NAME" && mkdir -p "$SERRA_DIR" || { problem sd_write_error; unlock; return 1; }
    if ! cp "$SERRA_RAM" "$SERRA_PART" || ! mv "$SERRA_PART" "$SERRA_FILE" || ! publish_meta; then
        problem sd_write_error; rm -f "$SERRA_RAM"; unlock; return 1
    fi
    rm -f "$SERRA_RAM"
    SERRA_USED=$((SERRA_USED + SERRA_COST)); SERRA_COUNT=$((SERRA_COUNT + 1)); usage_write
    [ "$SERRA_RECENT" = 0 ] || SERRA_LAST=$SERRA_NOW
    printf '%s %s %s %s %s\n' "$SERRA_LAST" "$((SERRA_NOW / 3600))" "$SERRA_ROTATION" "$SERRA_HASH" "$SERRA_CHANGED" > "$SERRA_CLOCK"
    printf '%s\n' "$SERRA_NOW" > "$SERRA_RUN/serra-spool.last"
    if [ "$SERRA_HISTORY" = 1 ]; then
        printf '%s %s\n' "$((SERRA_NOW / 3600))" "$SERRA_ROTATION" > "$SERRA_ROOT/.new-history-slot"
        mv "$SERRA_ROOT/.new-history-slot" "$SERRA_ROOT/.history-slot"
    fi
    problem ''
    # Firmware has only global sync; batch flushes to keep one-second capture inexpensive.
    SERRA_FLUSH=0; [ ! -f "$SERRA_RUN/serra-spool.flush" ] || read -r SERRA_FLUSH < "$SERRA_RUN/serra-spool.flush"
    if [ $((SERRA_NOW - SERRA_FLUSH)) -ge 10 ]; then sync; printf '%s\n' "$SERRA_NOW" > "$SERRA_RUN/serra-spool.flush"; fi
    unlock
}
headers() { printf 'Content-Type: %s\r\nCache-Control: no-store\r\n\r\n' "$1"; }
fail() { printf 'Status: %s\r\n' "$1"; headers application/json; printf '{"ok":false,"error":"%s"}\n' "$2"; exit 0; }
http() {
    config || fail '503 Service Unavailable' config_error
    SERRA_OP=''; SERRA_NAME=''
    SERRA_OLD_IFS=$IFS; IFS='&'
    for SERRA_PAIR in ${QUERY_STRING:-}; do
        case "$SERRA_PAIR" in op=*) SERRA_OP=${SERRA_PAIR#op=};; name=*) SERRA_NAME=${SERRA_PAIR#name=};; esac
    done
    IFS=$SERRA_OLD_IFS
    case "$SERRA_OP" in config|ack|capture)
        [ "${REQUEST_METHOD:-GET}" = POST ] || fail '405 Method Not Allowed' post_required
        SERRA_LENGTH=${CONTENT_LENGTH:-0}
        case "$SERRA_LENGTH" in ''|*[!0-9]*) fail '400 Bad Request' invalid_body;; esac
        [ "$SERRA_LENGTH" -le 8192 ] || fail '413 Content Too Large' invalid_body
        SERRA_BODY=$(dd bs=1 count="$SERRA_LENGTH" 2>/dev/null)
        ;;
    esac
    if [ "$SERRA_OP" = config ]; then
        read -r SERRA_NEW_ENABLED SERRA_NEW_INTERVAL SERRA_NEW_ROTATION SERRA_NEW_QUOTA SERRA_EXTRA <<EOF
$SERRA_BODY
EOF
        case "$SERRA_NEW_ENABLED" in 0|1) ;; *) fail '400 Bad Request' invalid_settings;; esac
        case "$SERRA_NEW_INTERVAL" in 1|5|10|15|30|60|120|300|600|900|1800|3600) ;; *) fail '400 Bad Request' invalid_settings;; esac
        case "$SERRA_NEW_ROTATION" in 0|90|180|270) ;; *) fail '400 Bad Request' invalid_settings;; esac
        case "$SERRA_NEW_QUOTA" in ''|*[!0-9]*) fail '400 Bad Request' invalid_settings;; esac
        [ -z "$SERRA_EXTRA" ] && [ "$SERRA_NEW_QUOTA" -ge 1048576 ] && [ "$SERRA_NEW_QUOTA" -le 16777216 ] || fail '400 Bad Request' invalid_settings
        lock
        { printf 'SERRA_ENABLED=%s\nSERRA_INTERVAL=%s\nSERRA_ROTATION=%s\nSERRA_QUOTA_KB=%s\n' "$SERRA_NEW_ENABLED" "$SERRA_NEW_INTERVAL" "$SERRA_NEW_ROTATION" "$SERRA_NEW_QUOTA"
          printf "SERRA_MOUNT='%s'\nSERRA_ROOT='%s'\nSERRA_IDENTITY='%s'\n" "$SERRA_MOUNT" "$SERRA_ROOT" "$SERRA_IDENTITY"; } > "$SERRA_CONFIG.part"
        mv "$SERRA_CONFIG.part" "$SERRA_CONFIG" || fail '503 Service Unavailable' config_error
        unlock; headers application/json; printf '{"ok":true}\n'; return
    fi
    if [ "$SERRA_OP" = capture ]; then
        snapshot force || fail '503 Service Unavailable' capture_error
        headers application/json; printf '{"ok":true}\n'; return
    fi
    if [ "$SERRA_OP" = manifest ]; then
        SERRA_ONLINE=false; SERRA_ERROR=sd_unavailable; SERRA_USED=0; SERRA_COUNT=0; SERRA_LAST=0
        if mounted; then
            SERRA_ONLINE=true; SERRA_ERROR=''; lock; usage_read || SERRA_ERROR=usage_error
            [ ! -f "$SERRA_STATUS" ] || read -r SERRA_ERROR < "$SERRA_STATUS"
            [ ! -f "$SERRA_CLOCK" ] || read -r SERRA_LAST SERRA_UNUSED < "$SERRA_CLOCK"
            [ ! -f "$SERRA_RUN/serra-spool.last" ] || read -r SERRA_LAST < "$SERRA_RUN/serra-spool.last"
            unlock
        fi
        headers text/plain
        printf '{"ok":true,"online":%s,"enabled":%s,"interval":%s,"rotation":%s,"quota_kb":%s,"used_kb":%s,"pending":%s,"last_capture":%s,"error":"%s"}\n' "$SERRA_ONLINE" "$SERRA_ENABLED" "$SERRA_INTERVAL" "$SERRA_ROTATION" "$SERRA_QUOTA_KB" "$SERRA_USED" "$SERRA_COUNT" "$SERRA_LAST" "$SERRA_ERROR"
        [ "$SERRA_ONLINE" = true ] || return 0
        lock; SERRA_LISTED=0
        for SERRA_FOLDER in "$SERRA_ROOT"/pending/[0-9]*; do
            [ -d "$SERRA_FOLDER" ] && [ ! -L "$SERRA_FOLDER" ] || continue
            for SERRA_ITEM in "$SERRA_FOLDER"/m-*.txt; do
                [ -f "$SERRA_ITEM" ] || continue
                SERRA_NAME=${SERRA_ITEM##*/}; SERRA_NAME=${SERRA_NAME#m-}; SERRA_NAME=${SERRA_NAME%.txt}.jpg
                paths "$SERRA_NAME" || continue
                read -r SERRA_HASH SERRA_SIZE SERRA_UNUSED < "$SERRA_META"
                printf '%s %s %s\n' "$SERRA_NAME" "$SERRA_SIZE" "$SERRA_HASH"
                SERRA_LISTED=$((SERRA_LISTED + 1)); [ "$SERRA_LISTED" -lt 24 ] || break 2
            done
        done
        unlock; return
    fi
    mounted || fail '503 Service Unavailable' sd_unavailable
    if [ "$SERRA_OP" = get ]; then
        paths "$SERRA_NAME" || fail '400 Bad Request' invalid_name
        lock
        [ -f "$SERRA_META" ] && [ -f "$SERRA_FILE" ] || fail '404 Not Found' missing_photo
        headers image/jpeg; cat "$SERRA_FILE"; unlock; return
    fi
    if [ "$SERRA_OP" = ack ]; then
        lock; usage_read || fail '503 Service Unavailable' usage_error
        # Validate the complete request before any deletion. A digest alone cannot acknowledge another file.
        SERRA_ACKS=0
        while read -r SERRA_NAME SERRA_DIGEST SERRA_EXTRA; do
            [ -n "$SERRA_NAME" ] || continue
            paths "$SERRA_NAME" && [ -z "$SERRA_EXTRA" ] || fail '400 Bad Request' invalid_ack
            SERRA_ACKS=$((SERRA_ACKS + 1)); [ "$SERRA_ACKS" -le 24 ] || fail '400 Bad Request' invalid_ack
            if [ -f "$SERRA_META" ]; then
                read -r SERRA_HASH SERRA_SIZE SERRA_COST < "$SERRA_META"
                [ "$SERRA_DIGEST" = "$SERRA_HASH" ] || fail '409 Conflict' digest_mismatch
            elif [ -f "$SERRA_FILE" ]; then fail '409 Conflict' incomplete_photo
            fi
        done <<EOF
$SERRA_BODY
EOF
        while read -r SERRA_NAME SERRA_DIGEST SERRA_EXTRA; do
            [ -n "$SERRA_NAME" ] || continue
            paths "$SERRA_NAME" || exit 1
            [ -f "$SERRA_META" ] || continue
            read -r SERRA_HASH SERRA_SIZE SERRA_COST < "$SERRA_META"
            # Content was committed on the server before it sent this authenticated acknowledgement.
            rm -f "$SERRA_META" "$SERRA_FILE" || fail '503 Service Unavailable' sd_write_error
            SERRA_USED=$((SERRA_USED - SERRA_COST)); SERRA_COUNT=$((SERRA_COUNT - 1))
            rmdir "$SERRA_DIR" 2>/dev/null || true
        done <<EOF
$SERRA_BODY
EOF
        [ "$SERRA_USED" -ge 0 ] || SERRA_USED=0; [ "$SERRA_COUNT" -ge 0 ] || SERRA_COUNT=0
        usage_write; sync; unlock
        headers application/json; printf '{"ok":true}\n'; return
    fi
    fail '400 Bad Request' invalid_operation
}
case "${1:-}" in
    run)
        config || exit 1
        SERRA_WAS_MOUNTED=0
        if recover; then SERRA_WAS_MOUNTED=1; else problem sd_unavailable; fi
        trap 'rm -f "$SERRA_RUN/serra-spool-image.jpg"; exit 0' TERM INT
        while :; do
            config
            if mounted; then
                [ "$SERRA_WAS_MOUNTED" = 1 ] || recover
                SERRA_WAS_MOUNTED=1
            else SERRA_WAS_MOUNTED=0
            fi
            snapshot tick
            # Recheck configuration every second; JPEG is requested only when a capture is due.
            if [ "$SERRA_INTERVAL" = 1 ]; then sleep 0.2; else sleep 1; fi
        done
        ;;
    http) http;;
    capture) snapshot force;;
    recover) config && recover;;
    *) exit 2;;
esac
