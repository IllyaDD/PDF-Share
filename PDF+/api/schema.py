from annotations.validators import (
    HEX_COLOR,
    MAX_GROUP_NAME_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_RECTS,
    MAX_SELECTED_TEXT_LENGTH,
)
from rest_framework import serializers

from .serializers import (
    AnnotationSerializer,
    BookSerializer,
    FolderSerializer,
    NoteGroupSerializer,
    ReadingProgressSerializer,
    RectSerializer,
    SharedAnnotationSerializer,
    SharedLayerSerializer,
)
from .sync import MAX_BATCH_OPS

API_PREFIX = "/api/v1/"


def only_v1(endpoints):
    """Preprocessing hook: у схемі лише /api/v1/.

    API читалки (/api/books/…) теж на DRF, але воно сесійне, не версіонується й деплоїться
    разом зі своїм JS — контрактом для встановлених застосунків воно не є.
    """
    return [endpoint for endpoint in endpoints if endpoint[0].startswith(API_PREFIX)]


def _ids(help_text):
    return serializers.ListField(child=serializers.IntegerField(), help_text=help_text)


# ── Помилки ─────────────────────────────────────────────────────────────────────


class ErrorSerializer(serializers.Serializer):
    error = serializers.CharField(help_text="Опис помилки українською, не для розбору клієнтом.")


class DetailSerializer(serializers.Serializer):
    """Помилки DRF і SimpleJWT: 401, 403, 404, 429."""

    detail = serializers.CharField()
    code = serializers.CharField(required=False)
    messages = serializers.ListField(
        child=serializers.DictField(), required=False, help_text="Лише в SimpleJWT."
    )


# ── Автентифікація ──────────────────────────────────────────────────────────────


class TokenUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()


class CredentialsSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField(help_text="Живе 30 хв.")
    refresh = serializers.CharField(help_text="Живе 30 днів; одноразовий — ротується.")
    user = TokenUserSerializer()


class GoogleIdTokenSerializer(serializers.Serializer):
    id_token = serializers.CharField(
        help_text="ID-токен нативного Google Sign-In; aud — web client ID або мобільний client ID."
    )


class GoogleTokenSerializer(TokenPairSerializer):
    created = serializers.BooleanField(help_text="true — акаунт щойно створено.")


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class RefreshSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(
        help_text="Новий refresh: попередній уже в blacklist, зберігати треба цей."
    )


# ── Синк: pull ──────────────────────────────────────────────────────────────────


class SyncPullSerializer(serializers.Serializer):
    server_time = serializers.DateTimeField(help_text="Зберегти й передати як since у наступному pull.")
    books = BookSerializer(many=True, help_text="Змінені після since (без since — усі видимі).")
    folders = FolderSerializer(many=True, help_text="Змінені після since разом із піддеревом.")
    annotations = AnnotationSerializer(many=True, help_text="Власні, змінені після since.")
    deleted_annotation_ids = _ids(
        "Видалені після since: власні й з опублікованих шарів вчителів. Без since — порожній."
    )
    note_groups = NoteGroupSerializer(
        many=True, help_text="Усі власні теми щоразу: теми, якої нема в списку, видалено."
    )
    shared_layers = SharedLayerSerializer(
        many=True, help_text="Усі видимі чужі шари щоразу: зниклий шар викинути з анотаціями."
    )
    shared_annotations = SharedAnnotationSerializer(
        many=True,
        help_text="Анотації чужих шарів, змінені після since; шар, що щойно став видимим, — повністю.",
    )
    reading_progress = ReadingProgressSerializer(many=True, help_text="Змінений після since.")
    favorite_book_ids = _ids("Обране, щоразу повністю.")
    visible_book_ids = _ids("Усі видимі книги: решту клієнт видаляє локально.")
    visible_folder_ids = _ids("Усі видимі теки: решту клієнт видаляє локально.")


# ── Синк: push, запит ───────────────────────────────────────────────────────────


class AnnotationCreateSerializer(serializers.Serializer):
    client_id = serializers.UUIDField(
        help_text="UUID з клієнта, ключ ідемпотентності: повтор повертає вже створену нотатку."
    )
    book = serializers.IntegerField(min_value=1)
    page_number = serializers.IntegerField(min_value=1)
    coords = RectSerializer(many=True, min_length=1, max_length=MAX_RECTS)
    color = serializers.RegexField(HEX_COLOR.pattern, help_text="#RRGGBB.")
    selected_text = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=MAX_SELECTED_TEXT_LENGTH
    )
    note_text = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=MAX_NOTE_LENGTH
    )
    group = serializers.IntegerField(
        required=False, allow_null=True, help_text="id власної теми на цій книзі; null — «Без групи»."
    )
    group_client_id = serializers.CharField(
        required=False, help_text="client_id теми, створеної в цьому ж пуші. Має перевагу над group."
    )


class AnnotationUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    color = serializers.RegexField(HEX_COLOR.pattern, required=False)
    note_text = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=MAX_NOTE_LENGTH
    )
    group = serializers.IntegerField(required=False, allow_null=True)
    group_client_id = serializers.CharField(required=False)


class AnnotationOpsSerializer(serializers.Serializer):
    create = AnnotationCreateSerializer(many=True, required=False)
    update = AnnotationUpdateSerializer(
        many=True, required=False, help_text="Last-write-wins за часом надходження пушу."
    )
    delete = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        help_text="id власних анотацій; вже видалена — не помилка.",
    )


class NoteGroupCreateSerializer(serializers.Serializer):
    client_id = serializers.CharField(
        required=False,
        help_text="Ключ, за яким нотатки цього пушу посилаються на тему (group_client_id).",
    )
    book = serializers.IntegerField(min_value=1)
    name = serializers.CharField(
        max_length=MAX_GROUP_NAME_LENGTH,
        help_text="Тема з такою назвою в цій книзі вже є — повертається вона, а не помилка.",
    )


class NoteGroupUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=MAX_GROUP_NAME_LENGTH)


class NoteGroupOpsSerializer(serializers.Serializer):
    create = NoteGroupCreateSerializer(many=True, required=False)
    update = NoteGroupUpdateSerializer(many=True, required=False)
    delete = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        help_text="Нотатки теми не видаляються, а стають «Без групи».",
    )


class ProgressItemSerializer(serializers.Serializer):
    book = serializers.IntegerField(min_value=1)
    last_page = serializers.IntegerField(min_value=1)
    read_at = serializers.DateTimeField(
        help_text="Коли читач був на сторінці, з часовим поясом. Перемагає новіший read_at."
    )


class FavoriteOpsSerializer(serializers.Serializer):
    add = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, help_text="Лише видимі книги."
    )
    remove = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)


class SyncChangesSerializer(serializers.Serializer):
    """Кожна операція незалежна: помилка елемента йде в errors, решта застосовується.

    Порядок обробки: видалення тем → перейменування → створення → анотації → прогрес → обране.
    """

    note_groups = NoteGroupOpsSerializer(required=False)
    annotations = AnnotationOpsSerializer(required=False)
    reading_progress = ProgressItemSerializer(many=True, required=False)
    favorites = FavoriteOpsSerializer(
        required=False,
        help_text="Фінальний стан, а не перемикач: id в add і remove водночас — помилка елемента.",
    )


# ── Синк: push, відповідь ───────────────────────────────────────────────────────


class PushedAnnotationSerializer(AnnotationSerializer):
    client_id = serializers.CharField(help_text="Рядок, як його надіслав клієнт.")


class PushedNoteGroupSerializer(NoteGroupSerializer):
    client_id = serializers.JSONField(allow_null=True, help_text="Як надіслав клієнт; null, якщо не було.")

    class Meta(NoteGroupSerializer.Meta):
        fields = [*NoteGroupSerializer.Meta.fields, "client_id"]


class NoteGroupResultSerializer(serializers.Serializer):
    created = PushedNoteGroupSerializer(many=True)
    updated = NoteGroupSerializer(many=True)
    deleted = _ids("id тем із запиту, оброблені без помилки.")


class FavoriteResultSerializer(serializers.Serializer):
    added = _ids("Застосовані add.")
    removed = _ids("Застосовані remove.")


# Операції в errors[] пушу.
PUSH_OPS = [
    "create", "update", "delete", "progress",
    "group_create", "group_update", "group_delete",
    "favorite_add", "favorite_remove",
]
PUSH_OP_CHOICES = [(op, op) for op in PUSH_OPS]


class PushErrorSerializer(serializers.Serializer):
    op = serializers.ChoiceField(choices=PUSH_OP_CHOICES)
    # Ключ — те значення, що прийшло від клієнта, тож тип будь-який (і null).
    id = serializers.JSONField(required=False, help_text="Для update, delete, group_update, group_delete.")
    client_id = serializers.JSONField(required=False, help_text="Для create, group_create.")
    book = serializers.JSONField(required=False, help_text="Для progress, favorite_add, favorite_remove.")
    error = serializers.CharField()


class SyncPushSerializer(serializers.Serializer):
    created = PushedAnnotationSerializer(many=True)
    updated = AnnotationSerializer(many=True)
    deleted = _ids("id анотацій із запиту, оброблені без помилки.")
    reading_progress = ReadingProgressSerializer(
        many=True, help_text="Фактичний стан після злиття: позиція могла програти новішій."
    )
    note_groups = NoteGroupResultSerializer()
    favorites = FavoriteResultSerializer()
    errors = PushErrorSerializer(many=True)
    server_time = serializers.DateTimeField()


PUSH_DESCRIPTION = (
    f"Батч змін, не більше {MAX_BATCH_OPS} операцій сумарно (інакше 400 на весь запит). "
    "Транзакція одна, але помилка окремого елемента не відкочує решту — вона в `errors`. "
    "Повтор того самого пушу безпечний: створення ідемпотентні за client_id, обране — "
    "встановлення стану."
)
