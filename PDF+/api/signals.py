from annotations.models import Annotation, Layer
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import DeletedAnnotation


@receiver(post_delete, sender=Annotation, dispatch_uid="api.record_deleted_annotation")
def record_deleted_annotation(sender, instance, **kwargs):
    owner_id = (
        Layer.objects.filter(pk=instance.layer_id).values_list("owner_id", flat=True).first()
    )
    if owner_id is not None:
        DeletedAnnotation.objects.create(
            annotation_id=instance.pk, owner_id=owner_id, layer_id=instance.layer_id
        )
