"""JSON-API читалки. Сесійна автентифікація, CSRF як у звичайних форм."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from book_library.access import visible_books

from .models import Annotation, Layer
from .serializers import serialize_annotation
from .validators import PayloadError, parse_annotation_update, parse_new_annotation


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise PayloadError("тіло запиту не є коректним JSON")


def _bad_request(error):
    return JsonResponse({"error": str(error)}, status=400)


@login_required
@require_http_methods(["GET", "POST"])
def book_annotations(request, book_pk):
    # Анотувати можна будь-яку видиму книгу — шар у кожного свій і чужу книгу не змінює.
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)

    if request.method == "GET":
        layer = Layer.objects.filter(book=book, owner=request.user).first()
        queryset = Annotation.objects.filter(layer=layer) if layer else Annotation.objects.none()
        return JsonResponse({"annotations": [serialize_annotation(a) for a in queryset]})

    try:
        fields = parse_new_annotation(_json_body(request), book.page_count)
    except PayloadError as error:
        return _bad_request(error)

    # Шар створюється лінькувато, при першій помітці. Унікальний констрейнт
    # (book, owner) робить гонку двох вкладок безпечною.
    layer, _ = Layer.objects.get_or_create(book=book, owner=request.user)
    annotation = Annotation.objects.create(layer=layer, **fields)

    return JsonResponse(serialize_annotation(annotation), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def annotation_detail(request, pk):
    annotation = get_object_or_404(Annotation, pk=pk, layer__owner=request.user)

    if request.method == "DELETE":
        annotation.delete()
        return JsonResponse({}, status=204)

    try:
        fields = parse_annotation_update(_json_body(request))
    except PayloadError as error:
        return _bad_request(error)

    for name, value in fields.items():
        setattr(annotation, name, value)
    annotation.save(update_fields=[*fields, "updated_at"])

    return JsonResponse(serialize_annotation(annotation))
