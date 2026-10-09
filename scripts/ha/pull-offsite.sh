#!/usr/bin/env bash
# Run on reserve. No production worker is started by this script.
set -euo pipefail
umask 077
root=/var/lib/neyro-offsite
config=/etc/neyro-offsite
test ! -e "$config/ACTIVE" || { echo 'Promoted reserve: refusing pull from former primary' >&2; exit 1; }
mkdir -p "$root/snapshots" "$root/images"
exec 9>/run/lock/neyro-reserve.lock
flock -n 9 || exit 0
test ! -e "$config/ACTIVE" || { echo 'Reserve promoted while waiting for lock' >&2; exit 1; }
stage=$(mktemp -d "$root/.verify.XXXXXXXX")
container="neyro-restore-check-$(date +%s)"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$stage"
}
trap cleanup EXIT
trap 'date -u +%FT%TZ > /var/lib/neyro-offsite/last-failure; echo "Backup/restore verification failed" >&2' ERR
# Avoid filling the system disk. Do not delete recent copies to conceal failure.
available=$(df -Pk "$root" | awk 'NR==2 {print $4}')
(( available > 4194304 )) || { echo 'Less than 4 GiB free' >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
ssh_args=(-i "$config/pull_key" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$config/known_hosts" -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
primary=$(cat "$config/primary")
ssh "${ssh_args[@]}" "$primary" snapshot > "$stage/snapshot.tar.gz.age"
age -d -i "$config/backup.agekey" "$stage/snapshot.tar.gz.age" | tar xzf - -C "$stage" ./database.dump ./image-id ./proxy-image-id ./snapshot-time ./SHA256SUMS ./release/Caddyfile
(cd "$stage" && sha256sum --check SHA256SUMS >/dev/null)
# Keep encrypted image archives and load the exact snapshot identities offline.
for image_file in image-id proxy-image-id; do
  image_id=$(cat "$stage/$image_file")
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Invalid snapshot image identity' >&2; exit 1; }
  archive="$root/images/${image_id#sha256:}.tar.age"
  if test ! -f "$archive"; then
    ssh "${ssh_args[@]}" "$primary" "image $image_id" > "$stage/image.tar.age"
    age -d -i "$config/backup.agekey" "$stage/image.tar.age" | docker image load >/dev/null
    test "$(docker image inspect "$image_id" --format '{{.Id}}')" = "$image_id"
    mv "$stage/image.tar.age" "$archive"
  elif ! docker image inspect "$image_id" >/dev/null 2>&1; then
    age -d -i "$config/backup.agekey" "$archive" | docker image load >/dev/null
  fi
  test "$(docker image inspect "$image_id" --format '{{.Id}}')" = "$image_id"
done
# Import only: no polling, scheduler, migrations or external requests.
docker run --rm --network none --read-only --memory 256m --pids-limit 64 \
  --cap-drop ALL --security-opt no-new-privileges:true --entrypoint python \
  "$(cat "$stage/image-id")" -c 'import os,asyncpg,fastapi,aiogram; assert os.getuid()==10001'
docker run --rm --network none --memory 128m --pids-limit 64 \
  --security-opt no-new-privileges:true -e SITE_ADDRESS=flipbazar.ru \
  -v "$stage/release/Caddyfile:/etc/caddy/Caddyfile:ro" \
  "$(cat "$stage/proxy-image-id")" caddy validate --config /etc/caddy/Caddyfile >/dev/null
# Restore into a disposable, network-isolated PostgreSQL. Never invoke the bot.
docker run -d --name "$container" --network none --memory 384m --pids-limit 128 \
  --security-opt no-new-privileges:true --tmpfs /var/lib/postgresql/data:rw,size=256m \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine >/dev/null
ready=0
for i in $(seq 1 30); do
  if docker exec "$container" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
test "$ready" = 1
docker exec "$container" createdb -U postgres restorecheck
docker exec -i "$container" pg_restore -U postgres -d restorecheck --no-owner --no-privileges --exit-on-error < "$stage/database.dump"
docker exec "$container" psql -U postgres -d restorecheck -v ON_ERROR_STOP=1 -Atc \
  'SELECT count(*) FROM users; SELECT count(*) FROM channels; SELECT count(*) FROM posts;' > "$stage/restore-counts"
# Share only the restore container's network namespace (it has --network none).
docker run --rm --network "container:$container" --read-only --memory 256m --pids-limit 64 \
  --tmpfs /tmp:rw,size=32m,mode=1777 --cap-drop ALL --security-opt no-new-privileges:true \
  -e PYTHON_DOTENV_DISABLED=1 -e BOT_TOKEN=1:offline-test -e LOGO_DIR=/tmp/logos \
  -e PYTHONPATH=/app -v /usr/local/lib/neyro-verify-restored-app.py:/verify.py:ro \
  --entrypoint python "$(cat "$stage/image-id")" /verify.py
# Publish only a fully verified archive. Retention is controlled on reserve only.
mv "$stage/snapshot.tar.gz.age" "$root/snapshots/$stamp.tar.gz.age"
cp "$stage/restore-counts" "$root/last-restore-counts"
cp "$stage/image-id" "$root/app-image-id"
cp "$stage/proxy-image-id" "$root/proxy-image-id"
printf '%s\n' "$stamp" > "$root/last-success.tmp"
mv "$root/last-success.tmp" "$root/last-success"
ln -sfn "snapshots/$stamp.tar.gz.age" "$root/latest.age"
find "$root/snapshots" -type f -name '*.tar.gz.age' -mtime +7 -delete
printf 'Verified encrypted snapshot: %s\n' "$stamp"

ssh "${ssh_args[@]}" "$primary" verified
