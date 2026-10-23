import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from book_library.models import Book, Folder


class ShareLink(models.Model):

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
    # Отримувач бачить шар нотаток автора посилання — лише для читання.
    include_annotations = models.BooleanField(_("з нотатками"), default=False)

    class AnnotationScope(models.TextChoices):
        ALL = "all", _("Усі нотатки")
        SELECTED = "selected", _("Лише обрані теми")

    # Явний режим, а не «порожній note_groups = усі»: якщо автор видалить усі обрані
    # теми, посилання має показувати нічого, а не весь шар.
    annotation_scope = models.CharField(
        _("які нотатки"), max_length=8, choices=AnnotationScope.choices, default=AnnotationScope.ALL
    )
    note_groups = models.ManyToManyField(
        "annotations.NoteGroup",
        blank=True,
        related_name="share_links",
        verbose_name=_("теми"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_sharelink"

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    def __str__(self):
        return self.token


class Group(models.Model):

    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_groups",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    invite_token = models.CharField(max_length=64, unique=True, null=True, blank=True)

    class Meta:
        db_table = "sharing_group"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def regenerate_invite_token(self):
        self.invite_token = secrets.token_urlsafe(32)
        self.save(update_fields=["invite_token"])


class GroupMembership(models.Model):

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


class GroupInvitation(models.Model):
    """Запрошення в групу за username: членство з'являється лише після згоди запрошеного.

    Прийняття створює `GroupMembership` і видаляє запрошення, відхилення — лише видаляє.
    Прав на матеріали запрошення не дає: `visible_books()` дивиться тільки на членство.
    """

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_group_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sharing_groupinvitation"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "user"],
                name="unique_invitation_per_group_user",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.group}"


class GroupAccess(models.Model):

    class Permission(models.TextChoices):
        VIEW = "view", _("Перегляд")
        EDIT = "edit", _("Редагування")

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
