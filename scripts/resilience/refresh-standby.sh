#!/usr/bin/env bash
# Refresh a dormant reserve only. Never starts workers or changes public DNS.
set -euo pipefail
umask 077
root=/var/lib/neyro-reserve
config=/etc/neyro-reserve
test ! -e "$config/ACTIVE"
exec 9>/run/lock/neyro-reserve.lock
flock -w 30 9
test ! -e "$config/ACTIVE" || { echo 'Reserve promoted while waiting for lock; refusing restore' >&2; exit 1; }
cd /opt/neyro
for service in app support caddy; do
  # A restarting/paused worker is not a dormant worker: it may resume publication.
  containers=$(docker compose ps --all -q "$service")
  for container in $containers; do
    state=$(docker inspect --format '{{.State.Status}}' "$container")
    case "$state" in
      exited|created|dead) ;;
      *) echo "$service is not dormant; refusing restore" >&2; exit 1 ;;
    esac
  done
done
archive=$(readlink -f "$root/latest.age")
stage=$(mktemp -d "$root/.refresh.XXXXXXXX")
trap 'rm -rf "$stage"' EXIT
age -d -i "$config/backup.agekey" "$archive" | tar xzf - --no-same-owner -C "$stage"
(cd "$stage" && sha256sum --check SHA256SUMS)
image_id=$(cat "$stage/image-id")
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]]
docker image inspect "$image_id" >/dev/null
# Retain reserve-only guard override; copy the release without executing its code.
cp -a "$stage/release/." /opt/neyro/
chmod 600 .env
docker tag "$image_id" neyro-posting:latest
# Older archives predate .app.env; derive it from the snapshot with secret filtering.
docker run --rm -i --network none --entrypoint python "$image_id" -c '
import io,json,sys
from dotenv import dotenv_values
for key,value in dotenv_values(stream=io.StringIO(sys.stdin.read())).items():
    if value is not None and not key.startswith(("POSTGRES_","APP_DATABASE_")) and key != "DATABASE_URL":
        print(key+"="+json.dumps(value,ensure_ascii=False))
' < .env > .app.env
chmod 600 .app.env
if test -f .support.env; then chmod 600 .support.env; fi
chown -R 10001:10001 data
chmod 700 data
if test -f "$stage/proxy-image-id"; then
  proxy_id=$(cat "$stage/proxy-image-id")
  [[ "$proxy_id" =~ ^sha256:[0-9a-f]{64}$ ]]
  docker image inspect "$proxy_id" >/dev/null
  docker tag "$proxy_id" neyro-caddy:recovery
fi
docker compose config --quiet
docker compose up -d --wait --wait-timeout 90 db
# Provision the application's limited role with the password in this snapshot.
# Administrative credentials are confined to this networkless one-shot helper.
docker run --rm --network none --env-file .env --entrypoint python "$image_id" -c '
import os
user=os.environ["APP_DATABASE_USER"]
assert user == "neyro_app"
password=os.environ["APP_DATABASE_PASSWORD"].replace("\x27", "\x27\x27")
print("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=\x27neyro_app\x27) THEN CREATE ROLE neyro_app LOGIN; END IF; END $$;")
print("ALTER ROLE neyro_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD \x27"+password+"\x27;")
' | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' > /dev/null
# The reserve database is disposable. The immutable encrypted snapshot is retained.
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges --exit-on-error' < "$stage/database.dump"
# Preserve original switches privately; disable all scheduled publications on recovery.
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT json_agg(t) FROM (SELECT id,paused,autopost,business_auto FROM channels ORDER BY id) t"' > "$config/pre-recovery-channels.json"
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
UPDATE channels SET paused=1,autopost=0,business_auto=0;
UPDATE broadcasts SET status='recovery_hold' WHERE status IN ('draft','running');
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO neyro_app;
DO $$ DECLARE r record; BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO neyro_app',r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname='public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO neyro_app',r.sequencename);
  END LOOP;
END $$;
SQL
cp "$stage/snapshot-time" "$config/prepared-snapshot"
printf '%s\n' 'Before starting workers: fence primary, review Telegram publications since snapshot, review queue and enable channels deliberately.' > "$config/RECOVERY_REVIEW_REQUIRED"
printf 'REFRESHED: latest snapshot restored; workers stopped; all channels paused\n'
