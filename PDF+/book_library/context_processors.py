from functools import cache

from django.db.models import Count, Q

from sharing.models import Group

from .models import Book, Folder


def sidebar(request):
    """Дані сайдбару: лічильник книг, кореневі теки й групи користувача.

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

    return {
        "sidebar_book_count": book_count,
        "sidebar_folders": folders,
        "sidebar_groups": groups,
    }
