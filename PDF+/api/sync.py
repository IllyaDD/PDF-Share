import uuid
from datetime import timedelta

from annotations.access import shared_annotations as readable_shared_annotations
from annotations.access import shared_layers
from annotations.groups import delete_note_group, group_fields, rename_note_group
from annotations.models import Annotation, Layer, LayerPublication, NoteGroup
from annotations.validators import (
    PayloadError,
    parse_annotation_update,
    parse_group_name,
    parse_new_annotation,
    parse_page_number,
)
from book_library.access import folders_subtree_ids, visible_books, visible_folders
from book_library.models import Book
from book_library.progress import record_progress, visible_progress
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from sharing.models import GroupAccess, GroupMembership

from .models import DeletedAnnotation
from .serializers import (
    AnnotationSerializer,
    BookSerializer,
    FolderSerializer,
    NoteGroupSerializer,
    ReadingProgressSerializer,
    SharedAnnotationSerializer,
    SharedLayerSerializer,
)

MAX_BATCH_OPS = 500


def _newly_granted(user, since):
    new_group_ids = GroupMembership.objects.filter(user=user, joined_at__gt=since).values_list(
        "group_id", flat=True
    )
    accesses = GroupAccess.objects.filter(group__memberships__user=user).filter(
        Q(created_at__gt=since) | Q(group_id__in=new_group_ids)
    )
    book_ids = set(accesses.exclude(book=None).values_list("book_id", flat=True))
    folder_ids = set(accesses.exclude(folder=None).values_list("folder_id", flat=True))
    return book_ids, folder_ids


def _newly_shared_layer_ids(user, since, layer_ids, changed_books):
    """Шари, що стали видимі після since: нова публікація, вступ у групу чи доступ до книги."""
    new_group_ids = GroupMembership.objects.filter(user=user, joined_at__gt=since).values_list(
        "group_id", flat=True
    )
    published = LayerPublication.objects.filter(
        Q(created_at__gt=since) | Q(group_id__in=new_group_ids),
        layer_id__in=layer_ids,
        group__memberships__user=user,
    ).values_list("layer_id", flat=True)
    by_book = Layer.objects.filter(
        pk__in=layer_ids, book__in=changed_books.values("pk")
    ).values_list("pk", flat=True)
    return set(published) | set(by_book)


def pull_state(user, since=None):
    server_time = timezone.now()
    books = visible_books(user).select_related("folder")
    folders = visible_folders(user)
    own_annotations = Annotation.objects.filter(layer__owner=user).select_related("layer")
    progress = visible_progress(user)
    layers = shared_layers(user).select_related("owner").order_by("pk")
    layer_ids = [layer.pk for layer in layers]
    # Без сесії посилання не діють — лишаються публікації для груп, тобто шари цілком.
    shared_annotations = readable_shared_annotations(user).select_related("layer")
    note_groups = NoteGroup.objects.filter(layer__owner=user).select_related("layer")

    if since is None:
        changed_books, changed_folders = books, folders
        changed_annotations = own_annotations
        changed_shared = shared_annotations
        changed_progress = progress
        deleted_ids = []
    else:
        since -= timedelta(seconds=settings.API_SYNC_OVERLAP_SECONDS)
        granted_book_ids, granted_folder_ids = _newly_granted(user, since)

        changed_roots = folders.filter(
            Q(updated_at__gt=since) | Q(pk__in=granted_folder_ids)
        ).values_list("pk", flat=True)
        subtree_ids = folders_subtree_ids(changed_roots)

        changed_folders = folders.filter(pk__in=subtree_ids)
        changed_books = books.filter(
            Q(updated_at__gt=since) | Q(pk__in=granted_book_ids) | Q(folder_id__in=subtree_ids)
        )
        changed_annotations = own_annotations.filter(updated_at__gt=since)
        # Шар, що щойно став видимим, їде повністю: його анотації могли змінитися давно.
        fresh_layer_ids = _newly_shared_layer_ids(user, since, layer_ids, changed_books)
        changed_shared = shared_annotations.filter(
            Q(updated_at__gt=since) | Q(layer_id__in=fresh_layer_ids)
        )
        changed_progress = progress.filter(updated_at__gt=since)
        deleted_ids = list(
            DeletedAnnotation.objects.filter(
                Q(owner=user) | Q(layer_id__in=layer_ids), deleted_at__gt=since
            )
            .order_by("annotation_id")
            .values_list("annotation_id", flat=True)
        )

    return {
        "server_time": server_time,
        "books": BookSerializer(changed_books, many=True).data,
        "folders": FolderSerializer(changed_folders, many=True).data,
        "annotations": AnnotationSerializer(changed_annotations, many=True).data,
        "deleted_annotation_ids": deleted_ids,
        # Тем мало, тож список щоразу повний: тема, якої нема, — видалена (без тумбстоунів).
        "note_groups": NoteGroupSerializer(note_groups, many=True).data,
        # Опубліковані шари вчителів їдуть повністю щоразу, як і visible_*_ids: зниклий
        # зі списку шар клієнт викидає разом з його анотаціями.
        "shared_layers": SharedLayerSerializer(layers, many=True).data,
        "shared_annotations": SharedAnnotationSerializer(changed_shared, many=True).data,
        "reading_progress": ReadingProgressSerializer(changed_progress, many=True).data,
        "visible_book_ids": sorted(books.values_list("pk", flat=True)),
        "visible_folder_ids": sorted(folders.values_list("pk", flat=True)),
    }


class BatchError(ValueError):
    pass


def _ops(section, name, prefix="annotations"):
    raw = section.get(name, [])
    if not isinstance(raw, list):
        raise BatchError(f"{prefix}.{name}: очікується список")
    return raw


def _int_id(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PayloadError(f"{field}: очікується додатне ціле число")
    return value


def _visible_book(user, item):
    book_id = _int_id(item.get("book"), "book")
    try:
        return visible_books(user).get(pk=book_id)
    except Book.DoesNotExist:
        raise PayloadError("book: книги нема або вона недоступна")


def _create_group(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    book = _visible_book(user, item)
    name = parse_group_name(item.get("name"))
    layer, _ = Layer.objects.get_or_create(book=book, owner=user)
    # Ту саму тему могли створити офлайн на іншому пристрої або пуш повторився: назва
    # унікальна в шарі, тож та сама назва — та сама тема, а не помилка, що губить нотатки.
    # get_or_create сам переживає гонку двох паралельних пушів (savepoint + повторний get).
    group, _ = NoteGroup.objects.get_or_create(layer=layer, name=name)
    return group


def _update_group(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    pk = _int_id(item.get("id"), "id")
    group = NoteGroup.objects.filter(pk=pk, layer__owner=user).first()
    if group is None:
        raise PayloadError("id: теми нема або вона не ваша")
    return rename_note_group(group, parse_group_name(item.get("name")))


def _uuid(value, field):
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise PayloadError(f"{field}: очікується UUID")


def _create(user, item, client_groups, released):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    client_id = _uuid(item.get("client_id"), "client_id")
    book = _visible_book(user, item)
    fields = parse_new_annotation(item, book.page_count)
    fields.update(group_fields(item, book.pk, user, client_groups, released))
    layer, _ = Layer.objects.get_or_create(book=book, owner=user)
    # Повтор пушу після обриву мережі (коміт пройшов, відповідь загубилась) повертає вже
    # створену нотатку, а не дубль. Ключ — у межах шару, тож UUID з іншої книги не зачепить.
    annotation, _ = Annotation.objects.get_or_create(
        layer=layer, client_id=client_id, defaults=fields
    )
    return annotation


def _record_progress(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    book = _visible_book(user, item)

    page = parse_page_number(item.get("last_page"), book.page_count, field="last_page")
    raw_read_at = item.get("read_at")
    read_at = parse_datetime(raw_read_at) if isinstance(raw_read_at, str) else None
    if read_at is None or read_at.tzinfo is None:
        raise PayloadError("read_at: очікується ISO 8601 з часовим поясом")
    return record_progress(user, book, page, read_at)


def _update(user, item, client_groups, released):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    pk = _int_id(item.get("id"), "id")
    annotation = Annotation.objects.filter(pk=pk, layer__owner=user).select_related("layer").first()
    if annotation is None:
        raise PayloadError("id: анотації нема або вона не ваша")

    changes = {k: v for k, v in item.items() if k not in ("id", "group_client_id")}
    fields = parse_annotation_update(changes) if changes else {}
    fields.update(group_fields(item, annotation.layer.book_id, user, client_groups, released))
    if not fields:
        raise PayloadError("нема що оновлювати")
    for name, value in fields.items():
        setattr(annotation, name, value)
    annotation.save(update_fields=[*fields, "updated_at"])
    return annotation


def _field(item, name):
    return item.get(name) if isinstance(item, dict) else None


def _each(items, op, key, key_of, apply, errors):
    """apply до кожного елемента; PayloadError іде в errors поштучно, а батч — далі."""
    results = []
    for item in items:
        try:
            results.append(apply(item))
        except PayloadError as error:
            errors.append({"op": op, key: key_of(item), "error": str(error)})
    return results


def _section(payload, key):
    section = payload.get(key, {})
    if not isinstance(section, dict):
        raise BatchError(f"{key}: очікується об'єкт із create/update/delete")
    return {name: _ops(section, name, key) for name in ("create", "update", "delete")}


def _push_note_groups(user, ops, errors):
    """Теми — до анотацій: нотатка може посилатися на тему з цього ж пушу (group_client_id).

    Порядок — видалення, перейменування, створення: так «видалив чи перейменував X і створив
    новий X» офлайн дає нову тему, а не чіпляється за стару. Повертає підсумок, теми за
    client_id і id тем, видалених у цьому пуші (для нотаток вони означають «Без групи»).
    """
    released = set()
    client_groups = {}

    def delete(pk):
        _int_id(pk, "delete")
        group = NoteGroup.objects.filter(pk=pk, layer__owner=user).first()
        if group is not None:
            delete_note_group(group)
            released.add(pk)
        return pk

    def update(item):
        return NoteGroupSerializer(_update_group(user, item)).data

    def create(item):
        group = _create_group(user, item)
        client_id = item["client_id"] if "client_id" in item else None
        if isinstance(client_id, (str, int)):
            client_groups[client_id] = group
        return {"client_id": client_id, **NoteGroupSerializer(group).data}

    summary = {
        "deleted": _each(ops["delete"], "group_delete", "id", lambda pk: pk, delete, errors),
        "updated": _each(
            ops["update"], "group_update", "id", lambda item: _field(item, "id"), update, errors
        ),
        "created": _each(
            ops["create"], "group_create", "client_id",
            lambda item: _field(item, "client_id"), create, errors,
        ),
    }
    return summary, client_groups, released


def _push_annotations(user, ops, client_groups, released, errors):
    def create(item):
        annotation = _create(user, item, client_groups, released)
        # client_id — як його надіслав клієнт, а не нормалізований UUID: клієнт шукає за своїм рядком.
        return {**AnnotationSerializer(annotation).data, "client_id": item["client_id"]}

    def update(item):
        return AnnotationSerializer(_update(user, item, client_groups, released)).data

    def delete(pk):
        _int_id(pk, "delete")
        annotation = Annotation.objects.filter(pk=pk, layer__owner=user).first()
        if annotation is not None:
            annotation.delete()
        return pk

    return {
        "created": _each(
            ops["create"], "create", "client_id", lambda item: _field(item, "client_id"),
            create, errors,
        ),
        "updated": _each(ops["update"], "update", "id", lambda item: _field(item, "id"), update, errors),
        "deleted": _each(ops["delete"], "delete", "id", lambda pk: pk, delete, errors),
    }


@transaction.atomic
def push_changes(user, payload):
    if not isinstance(payload, dict):
        raise BatchError("очікується JSON-об'єкт")
    annotation_ops = _section(payload, "annotations")
    group_ops = _section(payload, "note_groups")
    progress_items = payload.get("reading_progress", [])
    if not isinstance(progress_items, list):
        raise BatchError("reading_progress: очікується список")
    total = (
        sum(map(len, annotation_ops.values()))
        + sum(map(len, group_ops.values()))
        + len(progress_items)
    )
    if total > MAX_BATCH_OPS:
        raise BatchError(f"забагато операцій (максимум {MAX_BATCH_OPS})")

    errors = []
    note_groups, client_groups, released = _push_note_groups(user, group_ops, errors)
    annotations = _push_annotations(user, annotation_ops, client_groups, released, errors)
    # Фактичний стан після last-write-wins: клієнт бачить, якщо його позиція програла.
    progress = _each(
        progress_items, "progress", "book", lambda item: _field(item, "book"),
        lambda item: ReadingProgressSerializer(_record_progress(user, item)).data, errors,
    )
    return {
        **annotations,
        "reading_progress": progress,
        "note_groups": note_groups,
        "errors": errors,
        "server_time": timezone.now(),
    }
