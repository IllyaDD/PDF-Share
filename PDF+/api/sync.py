from datetime import timedelta

from annotations.models import Annotation, Layer
from annotations.validators import PayloadError, parse_annotation_update, parse_new_annotation
from book_library.access import folders_subtree_ids, visible_books, visible_folders
from book_library.models import Book
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from sharing.models import GroupAccess, GroupMembership

from .models import DeletedAnnotation
from .serializers import AnnotationSerializer, BookSerializer, FolderSerializer

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


def pull_state(user, since=None):
    server_time = timezone.now()
    books = visible_books(user).select_related("folder")
    folders = visible_folders(user)
    own_annotations = Annotation.objects.filter(layer__owner=user).select_related("layer")

    if since is None:
        changed_books, changed_folders = books, folders
        changed_annotations = own_annotations
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
        deleted_ids = list(
            DeletedAnnotation.objects.filter(owner=user, deleted_at__gt=since)
            .order_by("annotation_id")
            .values_list("annotation_id", flat=True)
        )

    return {
        "server_time": server_time,
        "books": BookSerializer(changed_books, many=True).data,
        "folders": FolderSerializer(changed_folders, many=True).data,
        "annotations": AnnotationSerializer(changed_annotations, many=True).data,
        "deleted_annotation_ids": deleted_ids,
        "visible_book_ids": sorted(books.values_list("pk", flat=True)),
        "visible_folder_ids": sorted(folders.values_list("pk", flat=True)),
    }


class BatchError(ValueError):
    pass


def _ops(section, name):
    raw = section.get(name, [])
    if not isinstance(raw, list):
        raise BatchError(f"annotations.{name}: очікується список")
    return raw


def _int_id(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PayloadError(f"{field}: очікується додатне ціле число")
    return value


def _create(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    book_id = _int_id(item.get("book"), "book")
    try:
        book = visible_books(user).get(pk=book_id)
    except Book.DoesNotExist:
        raise PayloadError("book: книги нема або вона недоступна")

    fields = parse_new_annotation(item, book.page_count)
    layer, _ = Layer.objects.get_or_create(book=book, owner=user)
    return Annotation.objects.create(layer=layer, **fields)


def _update(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    pk = _int_id(item.get("id"), "id")
    annotation = Annotation.objects.filter(pk=pk, layer__owner=user).select_related("layer").first()
    if annotation is None:
        raise PayloadError("id: анотації нема або вона не ваша")

    fields = parse_annotation_update({k: v for k, v in item.items() if k != "id"})
    for name, value in fields.items():
        setattr(annotation, name, value)
    annotation.save(update_fields=[*fields, "updated_at"])
    return annotation


@transaction.atomic
def push_changes(user, payload):
    if not isinstance(payload, dict):
        raise BatchError("очікується JSON-об'єкт")
    section = payload.get("annotations", {})
    if not isinstance(section, dict):
        raise BatchError("annotations: очікується об'єкт із create/update/delete")

    creates, updates, deletes = _ops(section, "create"), _ops(section, "update"), _ops(section, "delete")
    if len(creates) + len(updates) + len(deletes) > MAX_BATCH_OPS:
        raise BatchError(f"забагато операцій (максимум {MAX_BATCH_OPS})")

    result = {"created": [], "updated": [], "deleted": [], "errors": []}

    for item in creates:
        client_id = item.get("client_id") if isinstance(item, dict) else None
        try:
            annotation = _create(user, item)
        except PayloadError as error:
            result["errors"].append({"op": "create", "client_id": client_id, "error": str(error)})
            continue
        result["created"].append({"client_id": client_id, **AnnotationSerializer(annotation).data})

    for item in updates:
        pk = item.get("id") if isinstance(item, dict) else None
        try:
            annotation = _update(user, item)
        except PayloadError as error:
            result["errors"].append({"op": "update", "id": pk, "error": str(error)})
            continue
        result["updated"].append(AnnotationSerializer(annotation).data)

    for pk in deletes:
        try:
            _int_id(pk, "delete")
        except PayloadError as error:
            result["errors"].append({"op": "delete", "id": pk, "error": str(error)})
            continue
        annotation = Annotation.objects.filter(pk=pk, layer__owner=user).first()
        if annotation is not None:
            annotation.delete()
        result["deleted"].append(pk)

    result["server_time"] = timezone.now()
    return result
