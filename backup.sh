#!/usr/bin/env bash
# Щогодинний бекап бази. На сервері — у crontab користувача (crontab -e):
#   30 * * * * /home/illyad/pdfplus/backup.sh >> /home/illyad/pdfplus/backups/backup.log 2>&1
# Змінюєш розклад — зміни й MONITOR["schedule"] нижче, інакше Sentry сиплеться «missed».
#
# pg_dump у контейнері db → backups/*.dump → копія в R2, префікс db-backups/
# → check-in у Sentry Cron Monitor. Немає check-in-у вчасно — Sentry шле алерт, тож збій не мовчить.
#
# Зберігання: усі дампи — 48 годин (точність до години); дамп о 03:xx UTC — «щоденний»,
# лишається 14 днів на сервері й 30 днів у R2. Щоденний визначається за годиною в імені файлу.
#
# Куди в R2: якщо в .env задано BACKUP_R2_BUCKET + BACKUP_R2_ACCESS_KEY_ID/SECRET — в окремий
# бакет з окремим ключем (ключ сайту тоді не має доступу до бекапів). Інакше — у бакет сайту.
#
# Відновлення (ПЕРЕЗАПИСУЄ поточну базу):
#   docker compose stop web
#   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/<файл>.dump
#   docker compose start web

set -euo pipefail
cd "$(dirname "$0")"
umask 077   # дамп — це вся база, включно з хешами паролів

HOURLY_KEEP_HOURS=48
DAILY_KEEP_DAYS=14
DAILY_GLOB='pdfplus-*_03??.dump'   # дамп о 03:xx UTC — щоденний
mkdir -p backups
name="pdfplus-$(date -u +%Y-%m-%d_%H%M).dump"

echo "== $(date -u +%FT%TZ) бекап $name"

# Користувач і база — з оточення контейнера db (POSTGRES_*), а не з .env: bash не розбере
# рядки на кшталт DEFAULT_FROM_EMAIL=PDF+ <noreply@…> без лапок.
# Пишемо в .partial і перейменовуємо лише після перевірки — обірваний дамп не виглядатиме цілим.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/$name.partial"
docker compose exec -T db pg_restore --list < "backups/$name.partial" > /dev/null
mv "backups/$name.partial" "backups/$name"
echo "дамп: $(du -h "backups/$name" | cut -f1)"

find backups -name 'pdfplus-*.dump' ! -name "$DAILY_GLOB" -mmin +$((HOURLY_KEEP_HOURS * 60)) -delete
find backups -name "$DAILY_GLOB" -mtime +"$DAILY_KEEP_DAYS" -delete

# Копія за межі сервера + check-in. Через образ web: там уже є boto3 і sentry_sdk, а в .env —
# ключі R2 і DSN. --user: дамп має права 600 і належить користувачу хоста.
docker compose run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    -v "$PWD/backups:/backups:ro" \
    -e BACKUP_FILE="$name" \
    web python - <<'PY'
import datetime
import os
import re
import sys

import boto3
import sentry_sdk
from sentry_sdk.crons import capture_checkin

HOURLY_KEEP = datetime.timedelta(hours=48)
DAILY_KEEP = datetime.timedelta(days=30)
DAILY_NAME = re.compile(r"_03\d\d\.dump$")  # той самий «щоденний», що й DAILY_GLOB у bash
PREFIX = "db-backups/"
MONITOR = {
    "schedule": {"type": "crontab", "value": "30 * * * *"},
    "timezone": "UTC",
    "checkin_margin": 30,
    "max_runtime": 30,
}

env = os.environ
if env.get("SENTRY_DSN"):
    sentry_sdk.init(dsn=env["SENTRY_DSN"], environment=env.get("SENTRY_ENVIRONMENT", "production"))


def checkin(status):
    if env.get("SENTRY_DSN"):
        capture_checkin(monitor_slug="pdfplus-db-backup", status=status, monitor_config=MONITOR)
        sentry_sdk.flush(timeout=10)


name = env["BACKUP_FILE"]
if env.get("BACKUP_R2_BUCKET"):
    bucket = env["BACKUP_R2_BUCKET"]
    key_id, secret = env["BACKUP_R2_ACCESS_KEY_ID"], env["BACKUP_R2_SECRET_ACCESS_KEY"]
elif env.get("USE_R2", "").lower() == "true":
    bucket = env["R2_BUCKET"]
    key_id, secret = env["R2_ACCESS_KEY_ID"], env["R2_SECRET_ACCESS_KEY"]
else:
    print("R2 не налаштовано — копія лише локальна")
    checkin("ok")
    sys.exit(0)

try:
    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
    )
    s3.upload_file(f"/backups/{name}", bucket, PREFIX + name)
    print(f"R2: {bucket}/{PREFIX}{name}")

    now = datetime.datetime.now(datetime.UTC)
    old = [
        {"Key": obj["Key"]}
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX)
        for obj in page.get("Contents", [])
        if now - obj["LastModified"] > (DAILY_KEEP if DAILY_NAME.search(obj["Key"]) else HOURLY_KEEP)
    ]
    if old:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": old})
        print(f"R2: видалено старих копій: {len(old)}")
except Exception:
    checkin("error")
    raise

checkin("ok")
PY

echo "готово"
