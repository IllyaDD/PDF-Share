from annotations.models import Annotation
from book_library.models import Book, Folder
from rest_framework import serializers


class FolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Folder
        fields = ["id", "name", "parent", "owner", "is_catalog", "created_at", "updated_at"]


class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = [
            "id",
            "title",
            "folder",
            "owner",
            "page_count",
            "file_size",
            "extracted_text",
            "uploaded_at",
            "updated_at",
        ]


class CatalogBookSerializer(BookSerializer):

    folder_path = serializers.SerializerMethodField()

    class Meta(BookSerializer.Meta):
        fields = [*BookSerializer.Meta.fields, "folder_path"]

    def get_folder_path(self, book):
        return book.folder.path_label if book.folder_id else ""


class AnnotationSerializer(serializers.ModelSerializer):
    book = serializers.IntegerField(source="layer.book_id", read_only=True)

    class Meta:
        model = Annotation
        fields = [
            "id",
            "book",
            "page_number",
            "type",
            "coords",
            "selected_text",
            "note_text",
            "color",
            "created_at",
            "updated_at",
        ]
