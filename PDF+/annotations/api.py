import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from book_library.access import visible_books

from .access import shared_annotations, shared_layers
from .groups import create_note_group, delete_note_group, group_fields, rename_note_group
from .models import Annotation, Layer, NoteGroup
from .serializers import serialize_annotation, serialize_note_group, serialize_shared_layers
from .validators import (
    PayloadError,
    parse_annotation_update,
    parse_group_name,
    parse_new_annotation,
)


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise PayloadError("тіло запиту не є коректним JSON")


def _json_object(request):
    payload = _json_body(request)
    if not isinstance(payload, dict):
        raise PayloadError("очікується JSON-об'єкт")
    return payload


def _bad_request(error):
    return JsonResponse({"error": str(error)}, status=400)


@login_required
@require_http_methods(["GET", "POST"])
def book_annotations(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)

    if request.method == "GET":
        layer = Layer.objects.filter(book=book, owner=request.user).first()
        queryset = Annotation.objects.filter(layer=layer) if layer else Annotation.objects.none()
        return JsonResponse({"annotations": [serialize_annotation(a) for a in queryset]})

    try:
        payload = _json_body(request)
        fields = parse_new_annotation(payload, book.page_count)
        fields.update(group_fields(payload, book.pk, request.user))
    except PayloadError as error:
        return _bad_request(error)

    layer, _ = Layer.objects.get_or_create(book=book, owner=request.user)
    annotation = Annotation.objects.create(layer=layer, **fields)

    return JsonResponse(serialize_annotation(annotation), status=201)


@login_required
@require_http_methods(["GET"])
def book_shared_layers(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)
    layers = shared_layers(request.user, request.session).filter(book=book)
    annotations = shared_annotations(request.user, request.session).filter(layer__book=book)
    return JsonResponse({"layers": serialize_shared_layers(layers, annotations)})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def annotation_detail(request, pk):
    annotation = get_object_or_404(
        Annotation.objects.select_related("layer"), pk=pk, layer__owner=request.user
    )

    if request.method == "DELETE":
        annotation.delete()
        return JsonResponse({}, status=204)

    try:
        payload = _json_body(request)
        fields = parse_annotation_update(payload)
        fields.update(group_fields(payload, annotation.layer.book_id, request.user))
    except PayloadError as error:
        return _bad_request(error)

    for name, value in fields.items():
        setattr(annotation, name, value)
    annotation.save(update_fields=[*fields, "updated_at"])

    return JsonResponse(serialize_annotation(annotation))


@login_required
@require_http_methods(["GET", "POST"])
def book_note_groups(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)

    if request.method == "GET":
        groups = NoteGroup.objects.filter(layer__book=book, layer__owner=request.user)
        return JsonResponse({"groups": [serialize_note_group(g) for g in groups]})

    try:
        name = parse_group_name(_json_object(request).get("name"))
        group = create_note_group(book, request.user, name)
    except PayloadError as error:
        return _bad_request(error)
    return JsonResponse(serialize_note_group(group), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def note_group_detail(request, pk):
    group = get_object_or_404(NoteGroup, pk=pk, layer__owner=request.user)

    if request.method == "DELETE":
        delete_note_group(group)
        return JsonResponse({}, status=204)

    try:
        rename_note_group(group, parse_group_name(_json_object(request).get("name")))
    except PayloadError as error:
        return _bad_request(error)
    return JsonResponse(serialize_note_group(group))
