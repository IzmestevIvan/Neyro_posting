#!/usr/bin/env bash
set -euo pipefail
umask 077
# Only literal image IDs are accepted; no tags, paths or shell fragments.
if [[ "${SSH_ORIGINAL_COMMAND:-}" =~ ^image\ (sha256:[0-9a-f]{64})$ ]]; then
  exec 9>/run/lock/neyro-export.lock
  flock -w 120 9
  recipient=$(cat /etc/neyro-backup/recipient)
  docker image save "${BASH_REMATCH[1]}" | age -r "$recipient"
  exit
fi
case "${SSH_ORIGINAL_COMMAND:-}" in
  snapshot) exec /usr/local/sbin/neyro-export-backup ;;
  verified)
    test -f /etc/neyro-reserve/ACTIVE
    date -u +%Y%m%dT%H%M%SZ > /var/lib/neyro-reserve/active-offsite-last-success.tmp
    mv /var/lib/neyro-reserve/active-offsite-last-success.tmp /var/lib/neyro-reserve/active-offsite-last-success
    ;;
  *) echo 'Command denied' >&2; exit 64 ;;
esac
