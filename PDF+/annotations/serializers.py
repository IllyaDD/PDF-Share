"""Одна форма подання анотації для API і для початкових даних читалки."""


def serialize_annotation(annotation):
    return {
        "id": annotation.pk,
        "page": annotation.page_number,
        "coords": annotation.coords,
        "color": annotation.color,
        "note": annotation.note_text,
        "text": annotation.selected_text,
    }
