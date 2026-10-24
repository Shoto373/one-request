#!/bin/sh
# CI/CD на сервере (pull-модель): новый коммит в main → тесты → сборка → выкатка → проверка здоровья → откат при сбое.
# Запускается systemd-таймером odin-cd.timer раз в 2 минуты; вручную: deploy/cd.sh --force
# GitHub Actions в организации хакатона не запускаются (биллинг), поэтому CI/CD живёт здесь.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BRANCH=${DEPLOY_BRANCH:-main}
LOG=${REPO_DIR}/deploy/cd.log
cd "$REPO_DIR"

exec 9>/tmp/odin-cd.lock
flock -n 9 || exit 0  # предыдущий запуск ещё идёт

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
compose() { docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --profile monitoring "$@"; }
port=$(grep -E '^HTTP_PORT=' .env 2>/dev/null | cut -d= -f2); port=${port:-8080}

healthy() {
  i=0
  while [ $i -lt 30 ]; do
    if curl -fsS -m 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then return 0; fi
    i=$((i + 1)); sleep 3
  done
  return 1
}

git fetch -q origin "$BRANCH"
new=$(git rev-parse "origin/$BRANCH")
cur=$(cat deploy/.deployed 2>/dev/null || true)
if [ "$new" = "$cur" ] && [ "${1:-}" != "--force" ]; then exit 0; fi
tag=$(echo "$new" | cut -c1-12)
log "deploy start ${cur:-none} -> $new"

git checkout -q --detach "$new"

# CI: тесты в отдельном образе (без БД интеграционные пропускаются), сломанный коммит не выкатываем
if ! docker build -q --target test -t "odin-zapros/test:$tag" . >/dev/null \
   || ! docker run --rm "odin-zapros/test:$tag" >>"$LOG" 2>&1; then
  log "tests FAILED on $tag — deploy skipped, staying on ${cur:-none}"
  [ -n "$cur" ] && git checkout -q --detach "$cur"
  echo "$new" > deploy/.failed
  exit 1
fi

docker build -q -t "odin-zapros/app:$tag" . >/dev/null
APP_TAG=$tag compose up -d --remove-orphans >>"$LOG" 2>&1

if healthy; then
  echo "$new" > deploy/.deployed
  rm -f deploy/.failed
  log "deploy OK $tag"
  # чистим старые образы приложения и тестов, оставляем 3 последних
  for repo in odin-zapros/app odin-zapros/test; do
    docker images "$repo" --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' | sort -r | tail -n +4 | cut -f2 \
      | xargs -r docker rmi >/dev/null 2>&1 || true
  done
else
  log "health check FAILED on $tag — rolling back to ${cur:-none}"
  if [ -n "$cur" ]; then
    git checkout -q --detach "$cur"
    APP_TAG=$(echo "$cur" | cut -c1-12) compose up -d --remove-orphans >>"$LOG" 2>&1 || true
  fi
  echo "$new" > deploy/.failed
  exit 1
fi
