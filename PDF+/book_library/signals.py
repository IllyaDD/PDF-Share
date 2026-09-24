import logging

from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Book

logger = logging.getLogger(__name__)


def _delete_blob(storage, name):

    def _do():
        try:
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
            _delete_blob(blob.storage, blob.name)


@receiver(pre_save, sender=Book, dispatch_uid="book_library.delete_replaced_book_file")
def delete_replaced_book_file(sender, instance, **kwargs):
    if instance.pk is None:
        return
    old = Book.objects.filter(pk=instance.pk).values_list(*BLOB_FIELDS).first()
    if old is None:
        return
    for field, old_name in zip(BLOB_FIELDS, old):
        blob = getattr(instance, field)
        if old_name and old_name != blob.name:
            _delete_blob(blob.storage, old_name)
