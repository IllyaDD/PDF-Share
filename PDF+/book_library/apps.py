from django.apps import AppConfig


class BookLibraryConfig(AppConfig):
    name = 'book_library'
    verbose_name = 'Library'

    def ready(self):
        from . import signals  # noqa: F401 — реєструє receiver-и
