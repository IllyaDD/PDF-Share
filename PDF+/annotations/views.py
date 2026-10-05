from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from book_library.access import visible_books

from .access import publishable_groups
from .forms import LayerPublishForm
from .groups import delete_note_group, rename_note_group
from .models import Annotation, Layer, NoteGroup
from .validators import PayloadError, parse_group_name


@login_required
def book_annotations_view(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)
    layer = Layer.objects.filter(book=book, owner=request.user).first()

    annotations = list(
        Annotation.objects.filter(layer=layer).select_related("layer")
        if layer
        else Annotation.objects.none()
    )
    groups = list(layer.note_groups.all()) if layer else []

    # Теми за назвою (порожні теж — щоб їх можна було перейменувати чи видалити),
    # «Без групи» останньою і лише коли в ній щось є.
    sections = []
    for group in [*groups, None]:
        group_id = group.pk if group else None
        items = [a for a in annotations if a.group_id == group_id]
        if group is None and not items:
            continue
        sections.append(
            {
                "group": group,
                "count": len(items),
                "pages": [
                    (page_number, list(rows))
                    for page_number, rows in groupby(items, key=lambda a: a.page_number)
                ],
            }
        )

    return render(
        request,
        "annotations/annotation_list.html",
        {
            "book": book,
            "sections": sections,
            "show_group_headings": bool(groups),
            "total_count": len(annotations),
            "pages_with_notes": len({a.page_number for a in annotations}),
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


@login_required
@require_POST
def layer_publish_view(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)
    form = LayerPublishForm(request.POST, groups=publishable_groups(request.user, book))
    if not form.is_valid():
        messages.error(request, "Оберіть групи зі списку.")
        return redirect("book_library:book_detail", pk=book.pk)

    groups = form.cleaned_data["groups"]
    if groups:
        layer, _ = Layer.objects.get_or_create(book=book, owner=request.user)
    else:
        layer = Layer.objects.filter(book=book, owner=request.user).first()

    if layer is not None:
        # Повна заміна набору: знімаються й публікації для груп, що вже втратили доступ.
        layer.shared_with_groups.set(groups)

    if groups:
        names = ", ".join(group.name for group in groups)
        messages.success(request, f"Нотатки відкрито для груп: {names}.")
    else:
        messages.success(request, "Нотатки більше не видно жодній групі.")
    return redirect("book_library:book_detail", pk=book.pk)


def _own_group(request, pk):
    return get_object_or_404(
        NoteGroup.objects.select_related("layer"), pk=pk, layer__owner=request.user
    )


@login_required
@require_POST
def note_group_rename_view(request, pk):
    group = _own_group(request, pk)
    try:
        rename_note_group(group, parse_group_name(request.POST.get("name")))
    except PayloadError as error:
        messages.error(request, f"Не вдалося перейменувати тему: {error}")
    else:
        messages.success(request, "Тему перейменовано.")
    return redirect("annotations:book_annotations", book_pk=group.layer.book_id)


@login_required
@require_POST
def note_group_delete_view(request, pk):
    group = _own_group(request, pk)
    book_pk = group.layer.book_id
    delete_note_group(group)
    messages.success(request, "Тему видалено, її нотатки тепер «Без групи».")
    return redirect("annotations:book_annotations", book_pk=book_pk)
