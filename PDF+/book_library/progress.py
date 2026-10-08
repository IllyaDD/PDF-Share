from django.utils import timezone

from .access import visible_books
from .models import ReadingProgress


def visible_progress(user, session=None):
    """Прогрес користувача лише по книгах, які він досі бачить."""
    return ReadingProgress.objects.filter(
        user=user, book_id__in=visible_books(user, session).values("pk")
    )


def record_progress(user, book, page, read_at=None):
    """Записує позицію читача за правилом last-write-wins по read_at.

    Офлайн-клієнт може принести старішу позицію пізніше за веб, тому порівнюємо час
    читання, а не час приходу запиту. Майбутній час клієнта обрізаємо до поточного.
    """
    now = timezone.now()
    read_at = min(read_at or now, now)

    progress, created = ReadingProgress.objects.get_or_create(
        user=user, book=book, defaults={"last_page": page, "read_at": read_at}
    )
    if not created and read_at > progress.read_at:
        progress.last_page = page
        progress.read_at = read_at
        progress.save(update_fields=["last_page", "read_at", "updated_at"])
    return progress
