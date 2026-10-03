#!/usr/bin/env bash
# Keep PostgreSQL administrative credentials outside application containers.
set -euo pipefail
umask 077
test -f .env
stage=$(mktemp .app.env.XXXXXXXX)
trap 'rm -f "$stage"' EXIT
docker run --rm -i --network none --entrypoint python neyro-posting:latest -c '
import io,json,sys
from dotenv import dotenv_values
values=dotenv_values(stream=io.StringIO(sys.stdin.read()))
for key,value in values.items():
    if value is not None and not key.startswith(("POSTGRES_","APP_DATABASE_")) and key != "DATABASE_URL":
        print(key+"="+json.dumps(value,ensure_ascii=False))
' < .env > "$stage"
mv "$stage" .app.env
