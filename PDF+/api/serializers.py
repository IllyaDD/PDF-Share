import hashlib

from annotations.models import Annotation, Layer, NoteGroup
from book_library.models import Book, Folder, ReadingProgress
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from sharing.models import Group, GroupInvitation
from users.models import User


class RectSerializer(serializers.Serializer):
    x0 = serializers.FloatField()
    y0 = serializers.FloatField()
    x1 = serializers.FloatField()
    y1 = serializers.FloatField()


@extend_schema_field(RectSerializer(many=True))
class CoordsField(serializers.JSONField):
    """coords як є з JSONField — вивід не змінюється, а в схемі видно прямокутники."""


class MeSerializer(serializers.ModelSerializer):
    # Google-акаунт пароля не має: клієнт за цим ховає вхід і зміну пароля.
    has_password = serializers.BooleanField(source="has_usable_password", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "email_verified",
            "first_name",
            "last_name",
            "has_password",
            "date_joined",
        ]


class FolderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Folder
        fields = ["id", "name", "parent", "owner", "is_catalog", "created_at", "updated_at"]


class BookSerializer(serializers.ModelSerializer):
    has_cover = serializers.SerializerMethodField(
        help_text="Обкладинку віддає books/{id}/cover/. Рендериться з файлу, тож оновлюється з file_version."
    )
    file_version = serializers.SerializerMethodField(
        help_text="Змінилося — файл замінено: перекачати PDF і обкладинку."
    )

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
            "has_cover",
            "file_version",
            "uploaded_at",
            "updated_at",
        ]

    def get_has_cover(self, book) -> bool:
        return bool(book.cover)

    def get_file_version(self, book) -> str:
        # Ключ у сховищі — випадковий UUID (book_upload_path), тож хеш міняється лише із
        # заміною файлу; updated_at міняється й від перейменування чи переносу. Хеш, а не
        # сам ключ, — щоб не світити ключі сховища.
        return hashlib.sha256(book.file.name.encode()).hexdigest()[:16]


class CatalogBookSerializer(BookSerializer):

    folder_path = serializers.SerializerMethodField()

    class Meta(BookSerializer.Meta):
        fields = [*BookSerializer.Meta.fields, "folder_path"]

    def get_folder_path(self, book) -> str:
        return book.folder.path_label if book.folder_id else ""


class AnnotationSerializer(serializers.ModelSerializer):
    book = serializers.IntegerField(source="layer.book_id", read_only=True)
    coords = CoordsField(
        read_only=True,
        help_text=(
            "Прямокутники в PDF user space: точки, вісь y угору, відлік від MediaBox "
            "(як convertToPdfPoint у PDF.js). Масштаб читалки на них не впливає."
        ),
    )

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
            # Ключ ідемпотентності пушу; за ним клієнт зіставляє локальні записи з серверними.
            "client_id",
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
    # Без group: учні бачать нотатки вчителя без його тем. client_id — службовий ключ
    # пристрою автора, учням ні до чого.
    class Meta(AnnotationSerializer.Meta):
        fields = [
            f for f in AnnotationSerializer.Meta.fields if f not in ("group", "client_id")
        ] + ["layer"]


class ReadingProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingProgress
        fields = ["book", "last_page", "read_at", "updated_at"]


class GroupSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source="owner.username", read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "owner", "owner_username"]


class InvitationSerializer(serializers.ModelSerializer):
    group = GroupSerializer(read_only=True)
    invited_by_username = serializers.CharField(source="invited_by.username", read_only=True)

    class Meta:
        model = GroupInvitation
        fields = ["id", "group", "invited_by_username", "created_at"]


class JoinTokenSerializer(serializers.Serializer):
    token = serializers.CharField(
        max_length=64, help_text="Токен із посилання-запрошення /groups/join/<token>/."
    )


class JoinedGroupSerializer(serializers.Serializer):
    group = GroupSerializer()
    joined = serializers.BooleanField(help_text="false — користувач уже був у групі.")
