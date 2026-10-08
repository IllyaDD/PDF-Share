from annotations.models import Annotation, Layer, NoteGroup
from book_library.models import Book, Folder, ReadingProgress
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
            "group",
            "created_at",
            "updated_at",
        ]


class NoteGroupSerializer(serializers.ModelSerializer):
    book = serializers.IntegerField(source="layer.book_id", read_only=True)

    class Meta:
        model = NoteGroup
        fields = ["id", "book", "name", "created_at", "updated_at"]


class SharedLayerSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Layer
        fields = ["id", "book", "owner", "owner_username"]


class SharedAnnotationSerializer(AnnotationSerializer):
    # Без group: учні бачать нотатки вчителя без його тем.
    class Meta(AnnotationSerializer.Meta):
        fields = [f for f in AnnotationSerializer.Meta.fields if f != "group"] + ["layer"]


class ReadingProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingProgress
        fields = ["book", "last_page", "read_at", "updated_at"]
