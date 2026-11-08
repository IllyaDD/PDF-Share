import hashlib
import secrets
from pathlib import Path

from annotations.models import Annotation, Layer, NoteGroup
from book_library.access import folder_subtree_ids
from book_library.forms import apply_pdf_info
from book_library.models import Book, Folder, ReadingProgress
from book_library.pdf import inspect_pdf
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from sharing.models import Group, GroupInvitation, ShareLink
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



class _OwnRelatedField(serializers.PrimaryKeyRelatedField):
    owner_field = "owner"

    def get_queryset(self):
        return super().get_queryset().filter(**{self.owner_field: self.context["request"].user})


class OwnFolderField(_OwnRelatedField):
    def __init__(self, **kwargs):
        super().__init__(queryset=Folder.objects.all(), **kwargs)


class OwnBookField(_OwnRelatedField):
    def __init__(self, **kwargs):
        super().__init__(queryset=Book.objects.all(), **kwargs)


class OwnNoteGroupField(_OwnRelatedField):
    owner_field = "layer__owner"

    def __init__(self, **kwargs):
        super().__init__(queryset=NoteGroup.objects.select_related("layer"), **kwargs)


class BookCreateSerializer(serializers.ModelSerializer):
    """Завантаження PDF — через inspect_pdf(), як форма на сайті й адмінка."""

    file = serializers.FileField(help_text="PDF, не більше MAX_UPLOAD_SIZE (50 МБ), без пароля.")
    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Порожньо — назва з метаданих PDF, а нема їх — ім'я файлу.",
    )
    folder = OwnFolderField(required=False, allow_null=True, help_text="Власна тека; null — корінь.")

    class Meta:
        model = Book
        fields = ["file", "title", "folder"]

    def validate_file(self, uploaded):
        try:
            self._pdf_info = inspect_pdf(uploaded)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from None
        return uploaded

    def create(self, validated_data):
        uploaded = validated_data["file"]
        title = (
            validated_data.get("title", "").strip()
            or self._pdf_info.title
            or Path(uploaded.name).stem.strip()
            or "PDF"
        )
        book = Book(
            owner=self.context["request"].user,
            title=title[:255],
            folder=validated_data.get("folder"),
            file=uploaded,
        )
        apply_pdf_info(book, self._pdf_info, uploaded)
        book.save()
        return book


class BookUpdateSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=255)
    folder = OwnFolderField(allow_null=True, help_text="Власна тека; null — прибрати з теки.")

    class Meta:
        model = Book
        fields = ["title", "folder"]

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Назва не може бути порожньою.")
        return value


class FolderWriteSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=255)
    parent = OwnFolderField(required=False, allow_null=True, help_text="Власна тека; null — корінь.")
    is_catalog = serializers.BooleanField(
        required=False, help_text="Змінювати може лише персонал (is_staff), як на сайті."
    )

    class Meta:
        model = Folder
        fields = ["name", "parent", "is_catalog"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Назва не може бути порожньою.")
        return value

    def validate_parent(self, parent):
        # Тека чи її підтека в ролі батька — цикл: обходи вгору по parent не закінчились би.
        if parent is not None and self.instance is not None:
            if parent.pk in folder_subtree_ids(self.instance.pk):
                raise serializers.ValidationError("Теку не можна вкласти саму в себе чи в її підтеку.")
        return parent

    def validate_is_catalog(self, value):
        current = self.instance.is_catalog if self.instance is not None else False
        if value != current and not self.context["request"].user.is_staff:
            raise serializers.ValidationError("Каталог змінює лише персонал.")
        return value


# ── Публікація власного шару ────────────────────────────────────────────────────


class LayerPublicationsSerializer(serializers.Serializer):
    groups = GroupSerializer(many=True, help_text="Групи, учасники яких зараз бачать шар.")
    publishable_groups = GroupSerializer(
        many=True,
        help_text="Власні групи, що бачать книгу: лише їх можна передати в PUT.",
    )


class LayerPublicationsUpdateSerializer(serializers.Serializer):
    group_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        max_length=500,
        help_text="Повний набір груп: решта публікацій знімається. [] — сховати від усіх.",
    )


# ── Share-посилання ─────────────────────────────────────────────────────────────


class ShareLinkSerializer(serializers.ModelSerializer):
    """Власне share-посилання. Відкривається на сайті: у JWT-клієнта сесії нема, а доступ
    за посиланням живе саме в сесії. Застосунок віддає url браузеру (universal link)."""

    url = serializers.SerializerMethodField(help_text="Абсолютна адреса /s/<token>/ на сайті.")
    book = OwnBookField(required=False, allow_null=True, help_text="Власна книга; рівно одне з book/folder.")
    folder = OwnFolderField(required=False, allow_null=True, help_text="Власна тека з усім вкладеним.")
    note_groups = OwnNoteGroupField(
        many=True,
        required=False,
        help_text=(
            "Власні теми цієї книги — лише для посилання на книгу з include_annotations. "
            "Порожньо — усі нотатки."
        ),
    )
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = ShareLink
        fields = [
            "id",
            "url",
            "book",
            "folder",
            "include_annotations",
            "annotation_scope",
            "note_groups",
            "expires_at",
            "is_expired",
            "created_at",
        ]
        read_only_fields = ["annotation_scope", "created_at"]
        extra_kwargs = {
            "include_annotations": {
                "help_text": "Отримувач бачить шар нотаток автора посилання, лише для читання."
            },
            "annotation_scope": {"help_text": "selected — якщо передано note_groups, інакше all."},
            "expires_at": {"help_text": "З часовим поясом; null — безстрокове."},
        }

    def get_url(self, link) -> str:
        return self.context["request"].build_absolute_uri(
            reverse("sharing:share_view", args=[link.token])
        )

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Дата завершення не може бути в минулому.")
        return value

    def validate(self, attrs):
        book, folder = attrs.get("book"), attrs.get("folder")
        if bool(book) == bool(folder):
            raise serializers.ValidationError("Потрібне рівно одне: book або folder.")
        groups = attrs.get("note_groups") or []
        if groups:
            if not book or not attrs.get("include_annotations"):
                raise serializers.ValidationError(
                    {"note_groups": ["Теми — лише для посилання на книгу з include_annotations."]}
                )
            if any(group.layer.book_id != book.pk for group in groups):
                raise serializers.ValidationError(
                    {"note_groups": ["Обрані теми мають належати обраній книзі."]}
                )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        # Атомарно: якщо тему видалять між валідацією й set(), посилання не має лишитись
        # закомічене з annotation_scope=selected і без тем — тобто таке, що не показує нічого.
        groups = validated_data.pop("note_groups", [])
        link = ShareLink.objects.create(
            **validated_data,
            created_by=self.context["request"].user,
            token=secrets.token_urlsafe(32),
            # Явний режим, як у ShareLinkForm: якщо автор потім видалить усі обрані теми,
            # посилання показуватиме нічого, а не весь шар.
            annotation_scope=(
                ShareLink.AnnotationScope.SELECTED if groups else ShareLink.AnnotationScope.ALL
            ),
        )
        link.note_groups.set(groups)
        return link
