"""Ендпоінт /health/ для healthcheck-у Docker.

Зроблено middleware, а не в'юхою: healthcheck у контейнері ходить на localhost,
а на проді ALLOWED_HOSTS містить лише домен. CommonMiddleware викликає
request.get_host() і відповів би 400, тому цей middleware стоїть першим
і відповідає раніше за перевірку хоста, сесії та мову.
"""

import logging

from django.db import DatabaseError, connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)

HEALTH_PATH = "/health/"


def _ping_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def health_response():
    try:
        _ping_database()
    except DatabaseError:
        # warning, а не error: Sentry шле подію з ERROR, а healthcheck
        # повторюється щохвилини й засипав би квоту під час простою БД.
        logger.warning("Health check: база даних недоступна", exc_info=True)
        response = JsonResponse({"status": "unavailable"}, status=503)
    else:
        response = JsonResponse({"status": "ok"})
    response["Cache-Control"] = "no-store"
    return response


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == HEALTH_PATH:
            return health_response()
        return self.get_response(request)
