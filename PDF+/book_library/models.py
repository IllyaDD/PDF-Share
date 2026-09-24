import uuid

from django.conf import settings
from django.db import models


class Folder(models.Model):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=255)
    is_catalog = models.BooleanField(
        default=False,
        help_text="Тека каталогу: разом з усім вкладеним доступна кожному користувачу.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "library_folder"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def path_label(self):
        names = []
        node = self
        while node is not None:
            names.append(node.name)
            node = node.parent
        return " / ".join(reversed(names))


def book_upload_path(instance, filename):
    return f"books/{uuid.uuid4().hex}.pdf"


def cover_upload_path(instance, filename):
    return f"covers/{uuid.uuid4().hex}.jpg"


class Book(models.Model):

    folder = models.ForeignKey(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="books",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="books",
    )
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=book_upload_path)
    cover = models.FileField(upload_to=cover_upload_path, blank=True)
    page_count = models.PositiveIntegerField(default=0)
    file_size = models.PositiveIntegerField(default=0, help_text="Розмір файлу в байтах")
    extracted_text = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "library_book"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title
