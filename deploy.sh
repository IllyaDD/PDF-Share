#!/usr/bin/env bash
# Оновлення проду. Запуск на сервері з кореня репозиторію: ./deploy.sh
#
# Перший деплой — те саме, плюс після нього:
#   docker compose run --rm web python manage.py createsuperuser

set -euo pipefail
cd "$(dirname "$0")"

# compose бере ${PG_*}, ${CLOUDFLARE_TUNNEL_TOKEN} тощо з .env у корені, а налаштування Django
# лежать у PDF+/.env — symlink тримає їх в одному файлі.
if [[ ! -f .env ]]; then
    echo "Нема .env у корені. Один раз виконай: ln -s PDF+/.env .env" >&2
    exit 1
fi

# CD передає DEPLOY_SHA — коміт, що пройшов CI. git pull узяв би свіжіший master,
# який міг прилетіти за цей час і ще не перевірений.
if [[ -n "${DEPLOY_SHA:-}" ]]; then
    git fetch --quiet origin
    git merge --ff-only "$DEPLOY_SHA"
else
    git pull --ff-only
fi
export SENTRY_RELEASE="$(git rev-parse HEAD)"

docker compose build web
# Якщо міграція впаде, set -e зупинить скрипт до up — старий контейнер працює далі.
docker compose run --rm web python manage.py migrate --noinput
# --wait: без нього up повертає 0 одразу, навіть якщо gunicorn потім не підніметься.
# Healthcheck web — /health/ з Dockerfile; не дочекались — скрипт падає, CD червоний.
docker compose up -d --remove-orphans --wait --wait-timeout 120
docker image prune -f

docker compose ps
