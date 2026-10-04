#!/usr/bin/env bash
# This client receives only a dedicated forced-command key, never the root admin key.
set -euo pipefail
sha=${1:?full commit SHA required}
bundle=${2:?source bundle required}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
: "${DEPLOY_HOST:?}" "${DEPLOY_KEY_FILE:?}" "${DEPLOY_KNOWN_HOSTS_FILE:?}"
[[ "$DEPLOY_HOST" =~ ^[a-zA-Z0-9.-]+$ ]]
args=(-i "$DEPLOY_KEY_FILE" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$DEPLOY_KNOWN_HOSTS_FILE" -o ConnectTimeout=15 -o ServerAliveInterval=20)
checksum=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$bundle")
ssh "${args[@]}" "root@$DEPLOY_HOST" "upload $sha $checksum" < "$bundle"
ssh "${args[@]}" "root@$DEPLOY_HOST" "deploy $sha"
for ((i=0;i<80;i++)); do
  sleep 15
  result=$(ssh "${args[@]}" "root@$DEPLOY_HOST" "status $sha")
  printf '%s\n' "$result"
  status=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' <<< "$result")
  case "$status" in
    success) exit 0 ;;
    failed) exit 1 ;;
  esac
done
echo 'Release status timed out; inspect server status before retrying.' >&2
exit 1
