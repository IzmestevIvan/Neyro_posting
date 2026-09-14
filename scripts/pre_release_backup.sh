#!/usr/bin/env bash
# Run from /opt/neyro on the configured production host before a release.
set -euo pipefail
test "$PWD" = /opt/neyro
umask 077
mkdir -p /opt/neyro/backups
backup_dir=$(mktemp -d /opt/neyro/backups/release-20260914-XXXXXX)
docker compose exec -T db pg_dump -U neyro -d neyro -Fc < /dev/null > "$backup_dir/database.dump"
test -s "$backup_dir/database.dump"
docker compose exec -T db pg_restore --list < "$backup_dir/database.dump" > "$backup_dir/contents.txt"
tar czf "$backup_dir/code.tgz" app scripts docs requirements.txt requirements-dev.txt pytest.ini Dockerfile docker-compose.yml Caddyfile .dockerignore README.md
tar czf "$backup_dir/config-data.tgz" .env data
docker image tag neyro-posting:latest "neyro-posting:before-${backup_dir##*/}"
# This DB is a disposable restore check, never the live database.
restore_db="neyro_restore_${backup_dir##*-}"
docker compose exec -T db createdb -U neyro "$restore_db" < /dev/null
docker compose exec -T db pg_restore -U neyro -d "$restore_db" --exit-on-error < "$backup_dir/database.dump"
docker compose exec -T db psql -U neyro -d "$restore_db" -v ON_ERROR_STOP=1 -c 'SELECT count(*) AS restored_users FROM users; SELECT count(*) AS restored_channels FROM channels; SELECT count(*) AS restored_posts FROM posts;' < /dev/null
printf 'BACKUP_DIR=%s\nRESTORE_DB=%s\n' "$backup_dir" "$restore_db"
