from functools import reduce
from operator import or_

from django.db.models import F, Q

from book_library.access import folder_subtree_ids, live_share_links, visible_books
from sharing.models import Group, ShareLink

from .models import Annotation, Layer


def _annotation_links(session):
    return [link for link in live_share_links(session) if link.include_annotations]


def _link_layer_scope(link, prefix=""):
    """Шар автора посилання на книзі посилання чи в піддереві теки; prefix — шлях до Layer."""
    if link.book_id:
        scope = Q(**{f"{prefix}book_id": link.book_id})
    else:
        scope = Q(**{f"{prefix}book__folder_id__in": folder_subtree_ids(link.folder_id)})
    return scope & Q(**{f"{prefix}owner_id": link.created_by_id})


def _share_link_sources(session):
    """Шари авторів посилань «з нотатками» — на книгах, які ці посилання відкривають."""
    return [_link_layer_scope(link) for link in _annotation_links(session)]


def shared_layers(user, session=None):
    """Чужі шари для читання — лише на книгах, які користувач бачить.

    Два джерела: публікація для групи, де він учасник, і share-посилання «з нотатками»
    із сесії. Без сесії (DRF API) лишаються тільки групи.
    """
    authenticated = user is not None and user.is_authenticated
    sources = _share_link_sources(session)
    if authenticated:
        # Обидві умови про публікацію в одному Q, щоб вони стосувалися тієї самої групи:
        # учень у ній складається, а власник групи — автор шару.
        sources.append(
            Q(
                publications__group__memberships__user=user,
                publications__group__owner=F("owner"),
            )
        )
    if not sources:
        return Layer.objects.none()

    layers = Layer.objects.filter(
        reduce(or_, sources),
        book__in=visible_books(user, session).values("pk"),
    )
    if authenticated:
        layers = layers.exclude(owner=user)
    return layers.distinct()


def shared_annotations(user, session=None):
    """Чужі нотатки для читання — єдине місце, звідки їх беруть читалка, API й синк.

    Ті самі джерела, що в shared_layers(), але на рівні нотатки: посилання на книгу
    з обраними темами відкриває лише нотатки цих тем. Джерела об'єднуються через OR,
    тож публікація для групи (весь шар) перекриває обмеження посилання.
    """
    authenticated = user is not None and user.is_authenticated
    sources = []
    for link in _annotation_links(session):
        scope = _link_layer_scope(link, prefix="layer__")
        if link.book_id and link.annotation_scope == ShareLink.AnnotationScope.SELECTED:
            # Порожній набір (теми видалено) — жодної нотатки, а не весь шар.
            scope &= Q(group_id__in=list(link.note_groups.values_list("pk", flat=True)))
        sources.append(scope)
    if authenticated:
        sources.append(
            Q(
                layer__publications__group__memberships__user=user,
                layer__publications__group__owner=F("layer__owner"),
            )
        )
    if not sources:
        return Annotation.objects.none()

    annotations = Annotation.objects.filter(
        reduce(or_, sources),
        layer__book__in=visible_books(user, session).values("pk"),
    )
    if authenticated:
        annotations = annotations.exclude(layer__owner=user)
    return annotations.distinct()


def publishable_groups(user, book):
    """Групи користувача, учасники яких бачать книгу: доступ до книги, до теки над нею або каталог."""
    groups = Group.objects.filter(owner=user)

    ancestor_ids = []
    node = book.folder
    while node is not None:
        if node.is_catalog:
            return groups
        ancestor_ids.append(node.pk)
        node = node.parent

    return groups.filter(
        Q(accesses__book=book) | Q(accesses__folder_id__in=ancestor_ids)
    ).distinct()
