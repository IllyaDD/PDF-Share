#!/usr/bin/env bash
# Оновлення проду. Запуск на сервері з кореня репозиторію: ./deploy.sh
#
# Перший деплой — те саме, плюс після нього:
#   docker compose run --rm web python manage.py createsuperuser

set -euo pipefail
cd "$(dirname "$0")"

# compose бере ${PG_*}, ${DOMAIN} тощо з .env у корені, а налаштування Django
# лежать у PDF+/.env — symlink тримає їх в одному файлі.
if [[ ! -f .env ]]; then
    echo "Нема .env у корені. Один раз виконай: ln -s PDF+/.env .env" >&2
    exit 1
fi

git pull --ff-only
export SENTRY_RELEASE="$(git rev-parse HEAD)"

docker compose build web
# Якщо міграція впаде, set -e зупинить скрипт до up — старий контейнер працює далі.
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d --remove-orphans
docker image prune -f

docker compose ps
