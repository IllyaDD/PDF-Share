from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from book_library.access import visible_books

from .models import Annotation, Layer


@login_required
def book_annotations_view(request, book_pk):
    """Усі виділення користувача в одній книзі, згруповані за сторінками."""
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)
    layer = Layer.objects.filter(book=book, owner=request.user).first()

    annotations = list(
        Annotation.objects.filter(layer=layer).select_related("layer")
        if layer
        else Annotation.objects.none()
    )
    # Queryset уже впорядкований за (page_number, created_at) через Meta.ordering.
    annotations_by_page = [
        (page_number, list(items))
        for page_number, items in groupby(annotations, key=lambda a: a.page_number)
    ]

    return render(
        request,
        "annotations/annotation_list.html",
        {
            "book": book,
            "annotations_by_page": annotations_by_page,
            "total_count": len(annotations),
            "pages_with_notes": len(annotations_by_page),
            "with_notes_count": sum(1 for a in annotations if a.note_text),
            "reader_url": reverse("book_library:book_reader", args=[book.pk]),
            "crumbs": [
                {"url": reverse("book_library:library"), "label": "Бібліотека"},
                {"url": reverse("book_library:book_detail", args=[book.pk]), "label": book.title},
                {"url": "", "label": "Нотатки"},
            ],
            "page_title": "Мої нотатки",
            "page_subtitle": book.title,
        },
    )


@login_required
def annotation_delete_view(request, pk):
    annotation = get_object_or_404(Annotation, pk=pk, layer__owner=request.user)
    book_pk = annotation.layer.book_id
    if request.method == "POST":
        annotation.delete()
        messages.success(request, "Нотатку видалено.")
    return redirect("annotations:book_annotations", book_pk=book_pk)
