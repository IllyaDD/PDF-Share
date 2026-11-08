from django.db import transaction
from django.utils import timezone

from .access import folder_subtree_ids
from .models import Book


def delete_folder(folder):
    with transaction.atomic():
        Book.objects.filter(folder_id__in=folder_subtree_ids(folder.pk)).update(
            folder=None, updated_at=timezone.now()
        )
        folder.delete()
