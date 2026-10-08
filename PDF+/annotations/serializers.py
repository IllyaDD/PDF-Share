from collections import defaultdict


def serialize_annotation(annotation):
    return {
        "id": annotation.pk,
        "page": annotation.page_number,
        "coords": annotation.coords,
        "color": annotation.color,
        "note": annotation.note_text,
        "text": annotation.selected_text,
        "group": annotation.group_id,
    }


def serialize_note_group(group):
    return {"id": group.pk, "name": group.name}


def serialize_shared_layers(layers, annotations):
    """Чужі шари з нотатками, які читач має право бачити (див. access.shared_annotations).

    Поле group не віддаємо: отримувач бачить нотатки без тем автора.
    """
    by_layer = defaultdict(list)
    for annotation in annotations.order_by("page_number", "created_at"):
        item = serialize_annotation(annotation)
        del item["group"]
        by_layer[annotation.layer_id].append(item)
    return [
        {"id": layer.pk, "owner": layer.owner.username, "annotations": by_layer[layer.pk]}
        for layer in layers.select_related("owner").order_by("owner__username")
    ]
