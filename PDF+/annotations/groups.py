"""Операції з темами нотаток — спільні для веб-API, сторінки нотаток і DRF-синку."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Layer, NoteGroup
from .validators import PayloadError, parse_group_id

DUPLICATE_NAME = "name: тема з такою назвою вже є"

# Дубль ловить UniqueConstraint, а не попередня перевірка: між перевіркою й INSERT інший
# запит (подвійний Enter, паралельний пуш) встигає створити ту саму тему. Savepoint
# (atomic) не дає IntegrityError зламати зовнішню транзакцію — батч синку живе далі.


def create_note_group(book, owner, name):
    layer, _ = Layer.objects.get_or_create(book=book, owner=owner)
    try:
        with transaction.atomic():
            return NoteGroup.objects.create(layer=layer, name=name)
    except IntegrityError:
        raise PayloadError(DUPLICATE_NAME)


def rename_note_group(group, name):
    previous = group.name
    group.name = name
    try:
        with transaction.atomic():
            group.save(update_fields=["name", "updated_at"])
    except IntegrityError:
        group.name = previous
        raise PayloadError(DUPLICATE_NAME)
    return group


@transaction.atomic
def delete_note_group(group):
    """Нотатки лишаються «Без групи» з новим updated_at — так їх побачить дельта синку."""
    group.annotations.update(group=None, updated_at=timezone.now())
    group.delete()


def group_fields(payload, book_id, owner, client_groups=None, released_ids=frozenset()):
    """Поле group для створення чи зміни нотатки.

    Тема має належати шару owner на книзі book_id. client_groups — теми, створені
    в цьому ж пуші синку: {client_id: NoteGroup}; released_ids — id тем, видалених у цьому
    пуші: нотатка в такій темі стає «Без групи», а решта її змін не губиться.
    """
    if client_groups is not None and "group_client_id" in payload:
        try:
            group = client_groups[payload["group_client_id"]]
        except (KeyError, TypeError):
            raise PayloadError("group_client_id: такої теми нема в цьому пуші")
        if group.layer.book_id != book_id:
            raise PayloadError("group_client_id: тема з іншої книги")
        return {"group": group}

    if "group" not in payload:
        return {}
    group_id = parse_group_id(payload["group"])
    if group_id is None or group_id in released_ids:
        return {"group": None}
    group = NoteGroup.objects.filter(
        pk=group_id, layer__book_id=book_id, layer__owner=owner
    ).first()
    if group is None:
        raise PayloadError("group: теми нема або вона з іншої книги")
    return {"group": group}
