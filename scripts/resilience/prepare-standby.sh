#!/usr/bin/env bash
# Explicit one-time reserve preparation. No polling, publishing, or public ports.
set -euo pipefail
umask 077
test -f /etc/neyro-reserve/backup.agekey
test ! -e /etc/neyro-reserve/ACTIVE
test ! -e /opt/neyro || { echo '/opt/neyro already exists; refusing to overwrite' >&2; exit 1; }
root=/var/lib/neyro-reserve
test -e "$root/latest.age"
stage=$(mktemp -d "$root/.prepare.XXXXXXXX")
trap 'rm -rf "$stage"' EXIT
age -d -i /etc/neyro-reserve/backup.agekey "$root/latest.age" | tar xzf - -C "$stage"
(cd "$stage" && sha256sum --check SHA256SUMS)
image_id=$(cat "$stage/image-id")
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "$image_id" >/dev/null
mv "$stage/release" /opt/neyro
chmod 700 /opt/neyro
cd /opt/neyro
chmod 600 .env
test ! -f .app.env || chmod 600 .app.env
test ! -f .support.env || chmod 600 .support.env
chown -R 10001:10001 data
chmod 700 data
# All workers/proxy stay opt-in even after a host reboot or ordinary compose up.
cat > compose.override.yaml <<'YAML'
services:
  app:
    profiles: [active]
    restart: "no"
  support:
    profiles: [active]
    restart: "no"
  caddy:
    profiles: [active]
    restart: "no"
YAML
docker tag "$image_id" neyro-posting:latest
docker compose config --quiet
docker compose up -d --wait --wait-timeout 90 db
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --exit-on-error' < "$stage/database.dump"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM users; SELECT count(*) FROM channels; SELECT count(*) FROM posts;"'
cp "$stage/snapshot-time" /etc/neyro-reserve/prepared-snapshot
printf 'STANDBY_PREPARED: database restored; application/support/proxy not started\n'
