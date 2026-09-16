"""
Правила доступу до тек і книг.

Три джерела права на перегляд:
  1. власність — owner=user;
  2. GroupAccess — матеріал виданий групі, у якій користувач є учасником;
  3. ShareLink — користувач відкривав токен-посилання, і токен лежить у його сесії.

Право на зміну (перейменувати, видалити, замінити файл) має лише власник.
GroupAccess.EDIT поки що не розширює права на сам матеріал: власні шари анотацій
доступні будь-кому, хто бачить книгу, і чужу книгу вони не змінюють.
"""

from django.db.models import Q

from .models import Book, Folder

SESSION_TOKENS_KEY = "share_tokens"


def remember_share_token(session, token):
    """Кладе токен у сесію, щоб подальші сторінки бачили відкритий матеріал."""
    tokens = session.get(SESSION_TOKENS_KEY, [])
    if token not in tokens:
        session[SESSION_TOKENS_KEY] = tokens + [token]


def _live_share_links(session):
    from sharing.models import ShareLink

    tokens = session.get(SESSION_TOKENS_KEY, []) if session is not None else []
    if not tokens:
        return ShareLink.objects.none()

    links = ShareLink.objects.filter(token__in=tokens)
    return [link for link in links if not link.is_expired]


def _with_descendants(folder_ids):
    """Доступ до теки поширюється на все дерево під нею."""
    known = set(folder_ids)
    frontier = list(known)
    while frontier:
        children = list(
            Folder.objects.filter(parent_id__in=frontier)
            .exclude(pk__in=known)
            .values_list("pk", flat=True)
        )
        if not children:
            break
        known.update(children)
        frontier = children
    return known


def folder_subtree_ids(folder_id):
    """Тека разом з усіма вкладеними — для пошуку та підрахунків у межах теки."""
    return _with_descendants({folder_id})


def shared_folder_ids(user, session=None):
    """Теки, відкриті через групу або посилання, разом із вкладеними."""
    roots = set()

    if user is not None and user.is_authenticated:
        roots.update(
            Folder.objects.filter(
                group_accesses__group__memberships__user=user
            ).values_list("pk", flat=True)
        )

    roots.update(link.folder_id for link in _live_share_links(session) if link.folder_id)

    return _with_descendants(roots) if roots else set()


def shared_book_ids(user, session=None):
    """Книги, відкриті напряму — через GroupAccess на книгу або посилання на книгу."""
    ids = set()

    if user is not None and user.is_authenticated:
        ids.update(
            Book.objects.filter(
                group_accesses__group__memberships__user=user
            ).values_list("pk", flat=True)
        )

    ids.update(link.book_id for link in _live_share_links(session) if link.book_id)

    return ids


def visible_books(user, session=None):
    condition = Q(pk__in=shared_book_ids(user, session))

    folder_ids = shared_folder_ids(user, session)
    if folder_ids:
        condition |= Q(folder_id__in=folder_ids)

    if user is not None and user.is_authenticated:
        condition |= Q(owner=user)

    return Book.objects.filter(condition).distinct()


def visible_folders(user, session=None):
    condition = Q(pk__in=shared_folder_ids(user, session))

    if user is not None and user.is_authenticated:
        condition |= Q(owner=user)

    return Folder.objects.filter(condition).distinct()
