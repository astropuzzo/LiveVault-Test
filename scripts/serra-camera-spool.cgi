#!/bin/sh
# API key only: browser cookies/trusted-IP bypass cannot mutate the SD spool.
. /var/www/x/auth.sh
if ! verify_api_key; then
    printf 'Status: 401 Unauthorized\r\nContent-Type: application/json\r\n\r\n{"ok":false}\n'
    exit 0
fi
exec /usr/sbin/serra-camera-spool http
