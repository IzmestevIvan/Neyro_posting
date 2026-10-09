#!/usr/bin/env bash
# Forced SSH command. The reserve key may only export encrypted snapshots/images.
set -euo pipefail
umask 077
cd /opt/neyro
recipient=$(cat /etc/neyro-backup/recipient)
# The only parameterized command accepts two strict image IDs, never paths/shell.
if [[ "${SSH_ORIGINAL_COMMAND:-}" =~ ^image-delta\ (sha256:[0-9a-f]{64})\ (sha256:[0-9a-f]{64})$ ]]; then
  base_id=${BASH_REMATCH[1]}
  target_id=${BASH_REMATCH[2]}
  exec 9>/run/lock/neyro-export.lock
  flock -w 120 9
  /usr/local/sbin/neyro-image-transfer export "$base_id" "$target_id" | age -r "$recipient"
  exit
fi
case "${SSH_ORIGINAL_COMMAND:-snapshot}" in
  image)
    docker image save neyro-posting:latest | age -r "$recipient"
    exit
    ;;
  proxy-image)
    proxy_image=$(docker inspect neyro-caddy-1 --format '{{.Image}}')
    docker image save "$proxy_image" | age -r "$recipient"
    exit
    ;;
  snapshot) ;;
  *) echo 'Command denied' >&2; exit 64 ;;
esac
exec 9>/run/lock/neyro-export.lock
flock -w 120 9
stage=$(mktemp -d /var/tmp/neyro-export.XXXXXXXX)
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/release"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' < /dev/null > "$stage/database.dump"
docker compose exec -T db pg_restore --list < "$stage/database.dump" > /dev/null
# Snapshot only the deployed release; never copy Docker socket or backup archives.
cp -a app scripts requirements.txt Dockerfile docker-compose.yml Caddyfile .dockerignore .env "$stage/release/"
if test -f requirements.lock; then cp requirements.lock "$stage/release/"; fi
if test -f .app.env; then cp .app.env "$stage/release/"; fi
if test -f .support.env; then cp .support.env "$stage/release/"; fi
if test -f Dockerfile.caddy; then cp Dockerfile.caddy "$stage/release/"; fi
if test -d waf; then cp -a waf "$stage/release/"; fi
mkdir -p "$stage/release/data"
if test -d data/logos; then cp -a data/logos "$stage/release/data/"; fi
docker inspect neyro-app-1 --format '{{.Image}}' > "$stage/image-id"
docker inspect neyro-caddy-1 --format '{{.Image}}' > "$stage/proxy-image-id"
date -u +%Y-%m-%dT%H:%M:%SZ > "$stage/snapshot-time"
(cd "$stage" && sha256sum database.dump > SHA256SUMS)
tar czf - -C "$stage" . | age -r "$recipient"
