import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Book

logger = logging.getLogger(__name__)


def _delete_blob(storage, field, name):
    """Видаляє blob після коміту, якщо на нього вже не посилається жодна книга.

    Копія з каталогу (Book.source_book) ділить файл і обкладинку з оригіналом: без цієї
    перевірки видалення копії чи заміна в ній PDF стерли б файл каталогу для всіх. Перевірка —
    саме після коміту, коли видалення й нові копії вже в базі.
    """

    def _do():
        try:
            if Book.objects.filter(**{field: name}).exists():
                return
            storage.delete(name)
        except Exception:
            logger.exception("Не вдалося видалити %s зі сховища", name)

    transaction.on_commit(_do)


BLOB_FIELDS = ("file", "cover")


@receiver(post_delete, sender=Book, dispatch_uid="book_library.delete_book_file")
def delete_book_file(sender, instance, **kwargs):
    for field in BLOB_FIELDS:
        blob = getattr(instance, field)
        if blob.name:
            _delete_blob(blob.storage, field, blob.name)


@receiver(pre_delete, sender=Book, dispatch_uid="book_library.touch_library_copies")
def touch_library_copies(sender, instance, **kwargs):
    """Оригінал каталогу видаляють — копії лишаються, а source_book у них стане null.

    SET_NULL робиться оновленням queryset без updated_at, і дельта синку не побачила б
    зміненого source_book. Тому updated_at копій оновлюється тут, до SET_NULL.
    """
    Book.objects.filter(source_book=instance).update(updated_at=timezone.now())


@receiver(pre_save, sender=Book, dispatch_uid="book_library.remember_replaced_book_file")
def remember_replaced_book_file(sender, instance, **kwargs):
    """Запам'ятовує blob, які save() замінить; прибирає їх delete_replaced_book_file.

    Видаляти звідси не можна: поза транзакцією (book_edit_view без ATOMIC_REQUESTS)
    on_commit виконується одразу, ще до UPDATE, і перевірка посилань у _delete_blob
    знайшла б стару назву в рядку цієї ж книги — файл лишився б у сховищі назавжди.
    """
    instance._replaced_blobs = []
    if instance.pk is None:
        return
    old = Book.objects.filter(pk=instance.pk).values_list(*BLOB_FIELDS).first()
    if old is None:
        return
    for field, old_name in zip(BLOB_FIELDS, old):
        if old_name and old_name != getattr(instance, field).name:
            instance._replaced_blobs.append((field, old_name))


@receiver(post_save, sender=Book, dispatch_uid="book_library.delete_replaced_book_file")
def delete_replaced_book_file(sender, instance, **kwargs):
    for field, old_name in getattr(instance, "_replaced_blobs", ()):
        _delete_blob(getattr(instance, field).storage, field, old_name)
    instance._replaced_blobs = []
