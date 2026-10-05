from django.conf import settings
from django.db import models


class DeletedAnnotation(models.Model):

    annotation_id = models.PositiveBigIntegerField()
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="deleted_annotations",
    )
    # Не FK: шар може зникнути разом із книгою, а тумбстоун має пережити його.
    # За ним учні отримують видалення з опублікованого шару вчителя.
    layer_id = models.PositiveBigIntegerField(null=True, blank=True)
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "api_deletedannotation"
        indexes = [
            models.Index(fields=["owner", "deleted_at"]),
            models.Index(fields=["layer_id", "deleted_at"]),
        ]

    def __str__(self):
        return f"Анотація #{self.annotation_id} ({self.owner})"
