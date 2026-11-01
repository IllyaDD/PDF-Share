from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from book_library.models import Book


class Layer(models.Model):

    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="layers",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="layers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    shared_with_groups = models.ManyToManyField(
        "sharing.Group",
        through="LayerPublication",
        related_name="shared_layers",
        blank=True,
    )

    class Meta:
        db_table = "annotations_layer"
        constraints = [
            models.UniqueConstraint(
                fields=["book", "owner"],
                name="unique_layer_per_book_owner",
            )
        ]

    def __str__(self):
        return f"Шар {self.owner} — {self.book}"


class LayerPublication(models.Model):
    """Шар, відкритий учасникам групи для читання. Публікує лише власник групи."""

    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name="publications",
    )
    group = models.ForeignKey(
        "sharing.Group",
        on_delete=models.CASCADE,
        related_name="layer_publications",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "annotations_layerpublication"
        constraints = [
            models.UniqueConstraint(
                fields=["layer", "group"],
                name="unique_publication_per_layer_group",
            )
        ]

    def __str__(self):
        return f"{self.layer} → {self.group}"


class NoteGroup(models.Model):
    """Тема нотаток у межах шару: одна книга, один власник.

    Видаляти лише через annotations.groups.delete_note_group() — інакше SET_NULL
    не оновить updated_at нотаток, і дельта синку їх не побачить.
    """

    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name="note_groups",
    )
    name = models.CharField(_("назва"), max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "annotations_notegroup"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["layer", "name"],
                name="unique_note_group_per_layer",
            )
        ]

    def __str__(self):
        return self.name


class Annotation(models.Model):

    PALETTE = [
        ("#FFEB3B", _("Жовтий")),
        ("#A5D6A7", _("Зелений")),
        ("#90CAF9", _("Блакитний")),
        ("#F48FB1", _("Рожевий")),
        ("#FFCC80", _("Помаранчевий")),
    ]

    class Type(models.TextChoices):
        HIGHLIGHT = "highlight", _("Виділення")

    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    page_number = models.PositiveIntegerField()
    type = models.CharField(
        max_length=16,
        choices=Type.choices,
        default=Type.HIGHLIGHT,
    )
    coords = models.JSONField(
        help_text=_("Список прямокутників у PDF user space: [{x0, y0, x1, y1}, ...]"),
    )
    selected_text = models.TextField(blank=True)
    note_text = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#FFEB3B")
    # Тема нотатки; null — «Без групи».
    group = models.ForeignKey(
        NoteGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotations",
    )
    # Ключ ідемпотентності пушу синку: повтор того самого create не дублює нотатку.
    # null — нотатка з вебу, повторів там нема.
    client_id = models.UUIDField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "annotations_annotation"
        ordering = ["page_number", "created_at"]
        indexes = [
            models.Index(fields=["layer", "page_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["layer", "client_id"],
                name="unique_annotation_client_id_per_layer",
            )
        ]

    def __str__(self):
        return f"{self.get_type_display()} с.{self.page_number}"
