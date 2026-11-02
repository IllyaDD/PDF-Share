from functools import cache

from django.db.models import Count, Q

from sharing.models import Group

from .models import Book, Favorite, Folder


def sidebar(request):
    """Дані сайдбару: лічильник книг, кореневі теки й групи користувача; id обраних книг.

    Ліниві й кешовані на запит: сайдбар рендериться двічі (колонка й offcanvas),
    а сторінки без сайдбару (читалка, вхід) у базу не ходять зовсім.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    @cache
    def book_count():
        return Book.objects.filter(owner=user).count()

    @cache
    def folders():
        return list(
            Folder.objects.filter(owner=user, parent=None)
            .annotate(book_count=Count("books"))
            .order_by("name")
        )

    @cache
    def groups():
        return list(
            Group.objects.filter(Q(owner=user) | Q(memberships__user=user))
            .distinct()
            .order_by("name")
        )

    # Стан зірочки на картках — одним запитом на сторінку, а не анотацією в кожній в'юсі.
    # Без звуження за visible_books(): перевіряються лише книги, які сторінка вже показує.
    @cache
    def favorite_book_ids():
        return frozenset(Favorite.objects.filter(user=user).values_list("book_id", flat=True))

    return {
        "sidebar_book_count": book_count,
        "sidebar_folders": folders,
        "sidebar_groups": groups,
        "favorite_book_ids": favorite_book_ids,
    }
