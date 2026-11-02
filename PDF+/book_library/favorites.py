from .access import visible_books
from .models import Favorite


def visible_favorites(user, session=None):
    """Обране користувача лише по книгах, які він досі бачить.

    Запис про книгу, до якої відкликали доступ, лишається в базі: доступ повернуть —
    книга знову з'явиться в «Обраному».
    """
    return Favorite.objects.filter(
        user=user, book_id__in=visible_books(user, session).values("pk")
    )


def set_favorite(user, book, favorite):
    """Ставить або знімає зірочку. Ідемпотентно: повтор запиту нічого не ламає.

    Саме «встановити стан», а не «перемкнути»: подвійний клік чи повтор офлайн-пушу
    з перемикачем повернули б книгу в попередній стан.
    """
    if favorite:
        Favorite.objects.get_or_create(user=user, book=book)
    else:
        Favorite.objects.filter(user=user, book=book).delete()
    return favorite
