#!/usr/bin/env bash
set -euo pipefail
umask 077
case "${SSH_ORIGINAL_COMMAND:-}" in
  snapshot) exec /usr/local/sbin/neyro-export-backup ;;
  verified)
    test -f /etc/neyro-reserve/ACTIVE
    date -u +%Y%m%dT%H%M%SZ > /var/lib/neyro-reserve/active-offsite-last-success.tmp
    mv /var/lib/neyro-reserve/active-offsite-last-success.tmp /var/lib/neyro-reserve/active-offsite-last-success
    ;;
  *) echo 'Command denied' >&2; exit 64 ;;
esac
