from django.db.models import Q

from .models import Book, Folder

SESSION_TOKENS_KEY = "share_tokens"


def remember_share_token(session, token):
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
    return _with_descendants({folder_id})


def folders_subtree_ids(folder_ids):
    folder_ids = set(folder_ids)
    return _with_descendants(folder_ids) if folder_ids else set()


def catalog_folder_ids():
    roots = set(Folder.objects.filter(is_catalog=True).values_list("pk", flat=True))
    return _with_descendants(roots) if roots else set()


def catalog_root_folders():
    ids = catalog_folder_ids()
    return Folder.objects.filter(is_catalog=True).exclude(parent_id__in=ids)


def is_catalog_folder(folder):
    node = folder
    while node is not None:
        if node.is_catalog:
            return True
        node = node.parent
    return False


def shared_folder_ids(user, session=None):
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
        catalog_ids = catalog_folder_ids()
        if catalog_ids:
            condition |= Q(folder_id__in=catalog_ids)

    return Book.objects.filter(condition).distinct()


def visible_folders(user, session=None):
    condition = Q(pk__in=shared_folder_ids(user, session))

    if user is not None and user.is_authenticated:
        condition |= Q(owner=user) | Q(pk__in=catalog_folder_ids())

    return Folder.objects.filter(condition).distinct()
