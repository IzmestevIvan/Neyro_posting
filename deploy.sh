#!/usr/bin/env bash
# Заливает текущий код на сервер и перезапускает стек. .env на сервере не трогается.
set -euo pipefail

# Адрес прода держим вне репозитория: .deploy.env в .gitignore.
[ -f .deploy.env ] && . ./.deploy.env

SERVER="${SERVER:?задайте SERVER в .deploy.env, например root@203.0.113.10}"
KEY="${KEY:-$HOME/.ssh/neyro_deploy}"
REMOTE="${REMOTE:-/opt/neyro}"

echo "→ прогоняю тесты"
.venv/bin/python -m pytest tests/ -q

echo "→ отправляю код на $SERVER:$REMOTE"
COPYFILE_DISABLE=1 tar czf - \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='._*' \
  app tests docs scripts requirements.txt requirements-dev.txt pytest.ini \
  Dockerfile docker-compose.yml Caddyfile .dockerignore README.md \
  2>/dev/null | ssh -i "$KEY" "$SERVER" "mkdir -p $REMOTE && tar xzf - -C $REMOTE"

echo "→ пересобираю и перезапускаю"
ssh -i "$KEY" "$SERVER" "cd $REMOTE && docker compose up -d --build"

echo "→ статус"
ssh -i "$KEY" "$SERVER" "cd $REMOTE && docker compose ps && docker compose logs app --tail 5"
