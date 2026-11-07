#!/bin/sh
# Run once per minute as root ON THE CAMERA. Images/state stay in RAM.
# Requires the native burned-in timestamp: equal JPEG bytes mean stale cache.
SERRA_HEALTH_RUN=${SERRA_HEALTH_RUN:-/run}
SERRA_HEALTH_RESTART=${SERRA_HEALTH_RESTART:-/etc/init.d/S31prudynt}
exec 9>"$SERRA_HEALTH_RUN/serra-camera-health.lock"
flock -n 9 || exit 0
state=$SERRA_HEALTH_RUN/serra-camera-health.state
image=$SERRA_HEALTH_RUN/serra-camera-health.jpg
trap 'rm -f "$image"' EXIT
previous=''; failures=0; restart_at=0
if [ -f "$state" ]; then read -r previous failures restart_at < "$state"; fi
case "$failures:$restart_at" in *[!0-9:]*|:*) failures=0; restart_at=0;; esac
now=$(date +%s)
digest=''
if timeout 12 prudyntctl snapshot -c 0 > "$image" && [ -s "$image" ]; then
    digest=$(sha256sum "$image"); digest=${digest%% *}
fi
if [ -n "$digest" ] && [ "$digest" != "$previous" ]; then
    failures=0
else
    failures=$((failures + 1))
fi
if [ "$failures" -ge 2 ] && [ $((now - restart_at)) -ge 900 ]; then
    # Record the cooldown before invoking the potentially stuck service.
    restart_at=$now
    printf '%s %s %s\n' "${digest:-unavailable}" "$failures" "$restart_at" > "$state"
    logger -t serra-camera-health 'Snapshot stale/unavailable twice; restarting image service only'
    # S31 launches a long-lived daemon: it must never inherit this watchdog lock.
    timeout 60 "$SERRA_HEALTH_RESTART" restart 9>&-
    failures=0; digest=''
fi
printf '%s %s %s\n' "${digest:-unavailable}" "$failures" "$restart_at" > "$state"
