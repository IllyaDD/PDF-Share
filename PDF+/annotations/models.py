from django.conf import settings
from django.db import models

from book_library.models import Book


class Layer(models.Model):
    """Шар анотацій: один користувач — одна книга."""

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


class Annotation(models.Model):
    """Виділення тексту на сторінці PDF."""

    # Палітра читалки. Живе тут, щоб шаблон і валідатор брали її з одного місця.
    # Кольори світлі — під mix-blend-mode: multiply текст крізь них лишається читним.
    PALETTE = [
        ("#FFEB3B", "Жовтий"),
        ("#A5D6A7", "Зелений"),
        ("#90CAF9", "Блакитний"),
        ("#F48FB1", "Рожевий"),
        ("#FFCC80", "Помаранчевий"),
    ]

    class Type(models.TextChoices):
        HIGHLIGHT = "highlight", "Виділення"

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
        help_text="Список прямокутників у PDF user space: [{x0, y0, x1, y1}, ...]",
    )
    selected_text = models.TextField(blank=True)
    note_text = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#FFEB3B")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "annotations_annotation"
        ordering = ["page_number", "created_at"]
        indexes = [
            models.Index(fields=["layer", "page_number"]),
        ]

    def __str__(self):
        return f"{self.get_type_display()} с.{self.page_number}"
