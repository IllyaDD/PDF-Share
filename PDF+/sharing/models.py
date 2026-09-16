from django.conf import settings
from django.db import models
from django.utils import timezone

from book_library.models import Book, Folder


class ShareLink(models.Model):
    """Посилання на книгу або теку за токеном."""

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
    )
    token = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="share_links",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_sharelink"

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    def __str__(self):
        return self.token


class Group(models.Model):
    """Група користувачів (клас), якою володіє вчитель."""

    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_group"
        ordering = ["name"]

    def __str__(self):
        return self.name


class GroupMembership(models.Model):
    """Членство користувача в групі."""

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_groupmembership"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"],
                name="unique_membership_per_group_user",
            )
        ]

    def __str__(self):
        return f"{self.user} @ {self.group}"


class GroupAccess(models.Model):
    """Доступ групи до книги або теки."""

    class Permission(models.TextChoices):
        VIEW = "view", "Перегляд"
        EDIT = "edit", "Редагування"

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="accesses",
    )
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="group_accesses",
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="group_accesses",
    )
    permission = models.CharField(
        max_length=8,
        choices=Permission.choices,
        default=Permission.VIEW,
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="granted_accesses",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_groupaccess"
        constraints = [
            # NULL-и в Postgres не конфліктують між собою, тому рядок про теку
            # (book IS NULL) не заважає іншим рядкам про теки в тій самій групі.
            models.UniqueConstraint(
                fields=["group", "book"],
                name="unique_access_per_group_book",
            ),
            models.UniqueConstraint(
                fields=["group", "folder"],
                name="unique_access_per_group_folder",
            ),
        ]

    def __str__(self):
        return f"{self.group} -> {self.book or self.folder} ({self.get_permission_display()})"
