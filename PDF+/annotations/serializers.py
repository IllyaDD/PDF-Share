"""Серіалізатори читалки — формат, на який спирається static/js/reader.js.

Ключі коротші, ніж у api/serializers.py (page, note, text замість page_number, note_text,
selected_text): мобільний клієнт і читалка мають різні контракти. Лише на вихід — вхідний
payload перевіряє annotations/validators.py, спільний із синком.
"""

from collections import defaultdict

from rest_framework import serializers

from .models import Annotation, Layer, NoteGroup


class ReaderAnnotationSerializer(serializers.ModelSerializer):
    page = serializers.IntegerField(source="page_number", read_only=True)
    note = serializers.CharField(source="note_text", read_only=True)
    text = serializers.CharField(source="selected_text", read_only=True)

    class Meta:
        model = Annotation
        fields = ["id", "page", "coords", "color", "note", "text", "group"]
        read_only_fields = fields


class ReaderSharedAnnotationSerializer(ReaderAnnotationSerializer):
    # Без group: отримувач бачить нотатки без тем автора.
    class Meta(ReaderAnnotationSerializer.Meta):
        fields = [f for f in ReaderAnnotationSerializer.Meta.fields if f != "group"]
        read_only_fields = fields


class ReaderNoteGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = NoteGroup
        fields = ["id", "name"]
        read_only_fields = fields


class ReaderSharedLayerSerializer(serializers.ModelSerializer):
    """Чужий шар разом із нотатками, які читач має право бачити.

    Нотатки не беруться з layer.annotations: share-посилання з обраними темами відкриває
    лише частину шару (див. access.shared_annotations). Їх передають у
    context["annotations"] як {layer_id: [Annotation, ...]}.
    """

    owner = serializers.CharField(source="owner.username", read_only=True)
    annotations = serializers.SerializerMethodField()

    class Meta:
        model = Layer
        fields = ["id", "owner", "annotations"]
        read_only_fields = fields

    def get_annotations(self, layer):
        items = self.context["annotations"].get(layer.pk, [])
        return ReaderSharedAnnotationSerializer(items, many=True).data


def serialize_shared_layers(layers, annotations):
    by_layer = defaultdict(list)
    for annotation in annotations.order_by("page_number", "created_at"):
        by_layer[annotation.layer_id].append(annotation)
    return ReaderSharedLayerSerializer(
        layers.select_related("owner").order_by("owner__username"),
        many=True,
        context={"annotations": by_layer},
    ).data
