"""JSON-API читалки на DRF.

На відміну від api/ (JWT для мобільного клієнта), тут сесія й CSRF, як у форм сайту:
reader.js шле X-CSRFToken. Share-токени теж у сесії, тому request.session іде в
visible_books() — книга, відкрита за посиланням, доступна для власних нотаток.
"""

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from book_library.access import visible_books

from .access import shared_annotations, shared_layers
from .groups import create_note_group, delete_note_group, group_fields, rename_note_group
from .models import Annotation, Layer, NoteGroup
from .serializers import (
    ReaderAnnotationSerializer,
    ReaderNoteGroupSerializer,
    serialize_shared_layers,
)
from .validators import (
    PayloadError,
    parse_annotation_update,
    parse_group_name,
    parse_new_annotation,
)


class ReaderApiView(APIView):
    """Спільна база: сесійна автентифікація, лише JSON і одна форма помилки.

    Кожна помилка — {"error": "..."}: reader.js показує саме це поле, тож DRF-івський
    {"detail": ...} (404, 405, CSRF, битий JSON) переписується в той самий ключ.
    PayloadError з validators.py стає 400 без try/except у кожній в'юсі.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def handle_exception(self, exc):
        if isinstance(exc, PayloadError):
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        response = super().handle_exception(exc)
        if isinstance(response.data, dict) and "detail" in response.data:
            response.data = {"error": str(response.data["detail"])}
        return response

    def visible_book(self, book_pk):
        return get_object_or_404(visible_books(self.request.user, self.request.session), pk=book_pk)

    def json_object(self):
        if not isinstance(self.request.data, dict):
            raise PayloadError(_("очікується JSON-об'єкт"))
        return self.request.data


class BookAnnotationsView(ReaderApiView):
    def get(self, request, book_pk):
        book = self.visible_book(book_pk)
        layer = Layer.objects.filter(book=book, owner=request.user).first()
        queryset = Annotation.objects.filter(layer=layer) if layer else Annotation.objects.none()
        return Response({"annotations": ReaderAnnotationSerializer(queryset, many=True).data})

    def post(self, request, book_pk):
        book = self.visible_book(book_pk)
        payload = request.data
        fields = parse_new_annotation(payload, book.page_count)
        fields.update(group_fields(payload, book.pk, request.user))

        layer, _ = Layer.objects.get_or_create(book=book, owner=request.user)
        annotation = Annotation.objects.create(layer=layer, **fields)
        return Response(
            ReaderAnnotationSerializer(annotation).data, status=status.HTTP_201_CREATED
        )


class BookSharedLayersView(ReaderApiView):
    def get(self, request, book_pk):
        book = self.visible_book(book_pk)
        layers = shared_layers(request.user, request.session).filter(book=book)
        annotations = shared_annotations(request.user, request.session).filter(layer__book=book)
        return Response({"layers": serialize_shared_layers(layers, annotations)})


class AnnotationDetailView(ReaderApiView):
    # Чужий шар — лише для читання: фільтр за власником дає 404, а не 403.
    def get_annotation(self, pk):
        return get_object_or_404(
            Annotation.objects.select_related("layer"), pk=pk, layer__owner=self.request.user
        )

    def patch(self, request, pk):
        annotation = self.get_annotation(pk)
        payload = request.data
        fields = parse_annotation_update(payload)
        fields.update(group_fields(payload, annotation.layer.book_id, request.user))

        for name, value in fields.items():
            setattr(annotation, name, value)
        annotation.save(update_fields=[*fields, "updated_at"])
        return Response(ReaderAnnotationSerializer(annotation).data)

    def delete(self, request, pk):
        self.get_annotation(pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookNoteGroupsView(ReaderApiView):
    def get(self, request, book_pk):
        book = self.visible_book(book_pk)
        groups = NoteGroup.objects.filter(layer__book=book, layer__owner=request.user)
        return Response({"groups": ReaderNoteGroupSerializer(groups, many=True).data})

    def post(self, request, book_pk):
        book = self.visible_book(book_pk)
        name = parse_group_name(self.json_object().get("name"))
        group = create_note_group(book, request.user, name)
        return Response(ReaderNoteGroupSerializer(group).data, status=status.HTTP_201_CREATED)


class NoteGroupDetailView(ReaderApiView):
    def get_group(self, pk):
        return get_object_or_404(NoteGroup, pk=pk, layer__owner=self.request.user)

    def patch(self, request, pk):
        group = self.get_group(pk)
        rename_note_group(group, parse_group_name(self.json_object().get("name")))
        return Response(ReaderNoteGroupSerializer(group).data)

    def delete(self, request, pk):
        delete_note_group(self.get_group(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)
