from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = "api"
    verbose_name = "API"

    def ready(self):
        from . import signals  # noqa: F401 — реєструє receiver-и
