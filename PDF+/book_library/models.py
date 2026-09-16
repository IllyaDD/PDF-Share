from django.conf import settings
from django.db import models


class Folder(models.Model):
    """Тека бібліотеки. Може вкладатись у іншу теку (self-FK)."""

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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "library_folder"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def path_label(self):
        """«Батько / Дитина / Онук» — щоб відрізняти однойменні теки у списках."""
        names = []
        node = self
        while node is not None:
            names.append(node.name)
            node = node.parent
        return " / ".join(reversed(names))


class Book(models.Model):
    """PDF-книга. Сам файл лежить у MinIO, у БД — метадані."""

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
    file = models.FileField(upload_to="books/")
    page_count = models.PositiveIntegerField(default=0)
    file_size = models.PositiveIntegerField(default=0, help_text="Розмір файлу в байтах")
    extracted_text = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "library_book"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title
