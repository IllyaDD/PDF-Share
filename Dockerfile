# syntax=docker/dockerfile:1

# ── builder: лише залежності у /app/.venv ───────────────────────────────────
# Окремим шаром від коду: поки uv.lock не змінився, build бере цей шар з кешу.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# ── runtime ─────────────────────────────────────────────────────────────────
# Та сама база, що й у builder: .venv посилається на /usr/local/bin/python.
# slim (glibc), не alpine — PyMuPDF має готові wheels лише під glibc.
FROM python:3.13-slim AS runtime

# psycopg встановлений без [binary] і бере системну libpq.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=3

COPY --from=builder /app/.venv /app/.venv
COPY PDF+/ /app/src/
WORKDIR /app/src

# settings читають SECRET_KEY і PG_* без default, тож collectstatic без них упаде.
# Значення фіктивні й живуть лише в цій команді — в образ вони не потрапляють.
RUN SECRET_KEY=collectstatic-only PG_DB=x PG_USER=x PG_PASSWORD=x PG_HOST=x PG_PORT=5432 \
    python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# curl у slim нема. urlopen кидає виняток на 503 — Python виходить з кодом 1.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/', timeout=4)"]

# Кількість воркерів — WEB_CONCURRENCY (gunicorn читає її сам), можна перекрити в .env.
# timeout 120: inspect_pdf() синхронно розбирає PDF до 50 МБ і рендерить обкладинку.
# /dev/shm: heartbeat-файли воркерів на overlay-диску дають хибні таймаути.
# --no-control-socket: сокет для gunicornc (з 26.x) лягає в $HOME, а /app не
# записуваний для користувача app; керувати воркерами наживо нам не треба.
CMD ["gunicorn", "book_sharing_settings.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--worker-tmp-dir", "/dev/shm", \
     "--no-control-socket", \
     "--access-logfile", "-"]
