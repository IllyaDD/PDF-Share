import ipaddress
import secrets

from annotations.access import publishable_groups
from annotations.models import Layer
from book_library.access import catalog_folder_ids, visible_books
from book_library.folders import delete_folder
from book_library.library_copies import add_to_library
from book_library.models import Book, Folder
from book_library.views import _search_titles
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)
from sharing.models import Group, GroupAccess, GroupInvitation, GroupMembership, ShareLink
from sharing.views import _join_group
from users.google import UnverifiedEmail, google_account
from users.models import User

from . import schema as docs
from .errors import Code, error_response
from .serializers import (
    BookCreateSerializer,
    BookSerializer,
    BookUpdateSerializer,
    CatalogBookSerializer,
    FolderSerializer,
    FolderWriteSerializer,
    GroupAccessSerializer,
    GroupDetailSerializer,
    GroupWriteSerializer,
    InvitationSerializer,
    InviteUserSerializer,
    JoinedGroupSerializer,
    JoinTokenSerializer,
    LayerPublicationsSerializer,
    LayerPublicationsUpdateSerializer,
    MeSerializer,
    MemberSerializer,
    SentInvitationSerializer,
    ShareLinkSerializer,
)
from .sync import BatchError, pull_state, push_changes

ERROR = docs.ErrorSerializer
PATH_ID = OpenApiParameter("id", int, OpenApiParameter.PATH)
FIELD_ERRORS = OpenApiResponse(ERROR, description="code=invalid; помилки полів — у fields.")
THROTTLED = OpenApiResponse(ERROR, description="code=throttled: понад 10 запитів на хвилину з однієї IP.")


class AuthTokenThrottle(AnonRateThrottle):
    scope = "auth_token"

    def get_ident(self, request):
        # X-Forwarded-For не годиться: Cloudflare дописує IP у кінець того, що надіслав клієнт,
        # і новий префікс щоразу давав би новий ліміт. CF-Connecting-IP Cloudflare перезаписує,
        # а повз тунель до gunicorn не дістатися — порт назовні не опублікований.
        ip = request.headers.get("cf-connecting-ip") or request.META.get("REMOTE_ADDR", "")
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        # Провайдер видає IPv6 цілою /64: інакше кожна адреса з неї — окремий ліміт.
        if addr.version == 6:
            return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
        return str(addr)


class TokenPairSerializer(TokenObtainPairSerializer):

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data["user"] = {"id": user.pk, "username": user.username}
        return data


@extend_schema_view(
    post=extend_schema(
        operation_id="obtainToken",
        summary="Вхід за логіном і паролем",
        request=docs.CredentialsSerializer,
        responses={
            200: docs.TokenPairSerializer,
            400: FIELD_ERRORS,
            401: OpenApiResponse(ERROR, description="Невірний логін чи пароль або акаунт вимкнено."),
            429: THROTTLED,
        },
    )
)
class ObtainTokenView(TokenObtainPairView):

    serializer_class = TokenPairSerializer
    parser_classes = [JSONParser]
    throttle_classes = [AuthTokenThrottle]


class RefreshSerializer(TokenRefreshSerializer):

    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except User.DoesNotExist:
            # simplejwt шукає власника через objects.get() без обробки: refresh видаленого
            # акаунта давав 500. Пошук іде до ротації, тож нічого не встигає змінитись.
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            ) from None


@extend_schema_view(
    post=extend_schema(
        operation_id="refreshToken",
        summary="Нова пара токенів за refresh",
        request=docs.RefreshTokenSerializer,
        responses={
            200: docs.RefreshSerializer,
            400: FIELD_ERRORS,
            401: OpenApiResponse(
                ERROR, description="Refresh недійсний, прострочений, відкликаний або акаунта нема."
            ),
            429: THROTTLED,
        },
    )
)
class RefreshTokenView(TokenRefreshView):
    serializer_class = RefreshSerializer
    parser_classes = [JSONParser]
    throttle_classes = [AuthTokenThrottle]


@extend_schema_view(
    post=extend_schema(
        operation_id="logout",
        summary="Вихід з цього пристрою",
        description="Refresh іде в blacklist. Access не потрібен; виданий access діє до свого exp.",
        request=docs.RefreshTokenSerializer,
        responses={
            200: OpenApiResponse(OpenApiTypes.OBJECT, description="Порожній об'єкт {}."),
            400: FIELD_ERRORS,
            401: OpenApiResponse(ERROR, description="Refresh недійсний або вже відкликаний."),
        },
    )
)
class LogoutView(TokenBlacklistView):
    """Вихід з пристрою: його refresh іде в blacklist.

    Автентифікація — сам refresh, а не access: той міг уже прострочитись. Виданий access
    живе до кінця свого строку (до 30 хв), бо перевіряється лише підписом.
    """

    parser_classes = [JSONParser]


def revoke_refresh_tokens(user):
    """Кладе в blacklist усі ще чинні refresh користувача — вихід на всіх пристроях."""
    tokens = OutstandingToken.objects.filter(
        user=user, expires_at__gt=timezone.now(), blacklistedtoken__isnull=True
    )
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in tokens], ignore_conflicts=True
    )


class LogoutAllView(APIView):
    """Вихід на всіх пристроях — на випадок утраченого телефона.

    Відкликає лише refresh, видані після ввімкнення blacklist: старіші не записані
    в OutstandingToken. Їх, як і раніше, відкликає зміна пароля.
    """

    @extend_schema(
        operation_id="logoutAll",
        summary="Вихід на всіх пристроях",
        request=None,
        responses={204: None},
    )
    def post(self, request):
        revoke_refresh_tokens(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):

    @extend_schema(operation_id="getMe", summary="Профіль", responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    @extend_schema(
        operation_id="deleteMe",
        summary="Видалити акаунт з усіма даними",
        description="Незворотно. Спершу відкликає всі refresh користувача.",
        responses={204: None},
    )
    def delete(self, request):
        """Видалення акаунта з застосунку — вимога App Store (5.1.1(v)) і Google Play.

        Те саме, що на вебі (users.views.delete_profile_view): каскад забирає книги, теки,
        шари й групи, а файли у сховищі прибирають сигнали book_library після коміту.
        """
        user = request.user
        with transaction.atomic():
            # До видалення: OutstandingToken.user — SET_NULL, потім токени вже не знайти.
            revoke_refresh_tokens(user)
            user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GoogleTokenView(APIView):
    """ID-токен нативного Google Sign-In → пара JWT. Google-юзер пароля не має."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AuthTokenThrottle]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="googleSignIn",
        summary="Вхід через Google",
        description=(
            "Акаунт шукається за поштою, підтвердженою Google; нема — створюється без пароля. "
            "Існуючий акаунт з непідтвердженою поштою втрачає пароль і видані токени."
        ),
        request=docs.GoogleIdTokenSerializer,
        responses={
            200: docs.GoogleTokenSerializer,
            400: OpenApiResponse(ERROR, description="Нема id_token."),
            401: OpenApiResponse(ERROR, description="Токен недійсний або прострочений."),
            403: OpenApiResponse(
                ERROR, description="Google не підтвердив пошту або акаунт вимкнено."
            ),
            429: THROTTLED,
            503: OpenApiResponse(
                ERROR, description="Google недоступний або вхід не налаштований."
            ),
        },
    )
    def post(self, request):
        raw = request.data.get("id_token")
        if not isinstance(raw, str) or not raw:
            return error_response(
                "id_token: очікується рядок", Code.GOOGLE_TOKEN_MISSING, status.HTTP_400_BAD_REQUEST
            )
        audiences = settings.GOOGLE_TOKEN_AUDIENCES
        # Без aud бібліотека не перевіряє отримувача, і токен будь-якого чужого
        # Google-застосунку відкрив би акаунт. Тому порожній список — відмова, а не пропуск.
        if not audiences:
            return error_response(
                "вхід через Google не налаштований",
                Code.GOOGLE_NOT_CONFIGURED,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            # Підпис ключами Google, iss, exp і aud перевіряє бібліотека.
            claims = id_token.verify_oauth2_token(raw, google_requests.Request(), audience=audiences)
        except TransportError:
            return error_response(
                "Google тимчасово недоступний", Code.GOOGLE_UNAVAILABLE,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except (ValueError, GoogleAuthError):
            return error_response(
                "id_token: недійсний або прострочений", Code.GOOGLE_TOKEN_INVALID,
                status.HTTP_401_UNAUTHORIZED,
            )
        try:
            user, created, _reset = google_account(claims)
        except UnverifiedEmail:
            return error_response(
                "Google не підтвердив адресу пошти", Code.GOOGLE_EMAIL_UNVERIFIED,
                status.HTTP_403_FORBIDDEN,
            )
        if not user.is_active:
            return error_response("акаунт вимкнено", Code.ACCOUNT_DISABLED, status.HTTP_403_FORBIDDEN)

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": {"id": user.pk, "username": user.username},
                "created": created,
            }
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="listCatalogBooks",
        summary="Книги каталогу",
        parameters=[
            OpenApiParameter("search", str, description="Підрядок назви, без урахування регістру."),
        ],
    )
)
class CatalogBooksView(ListAPIView):

    serializer_class = CatalogBookSerializer

    def get_queryset(self):
        queryset = (
            Book.objects.filter(folder_id__in=catalog_folder_ids())
            .select_related("folder__parent__parent")
            .order_by("title", "pk")
        )
        query = self.request.query_params.get("search", "").strip()
        return _search_titles(queryset, query) if query else queryset


class CatalogBookAddView(APIView):

    @extend_schema(
        operation_id="addCatalogBookToLibrary",
        summary="Додати книгу каталогу у свою бібліотеку",
        description=(
            "Створює власну копію в корені бібліотеки: далі це звичайна книга користувача "
            "(перейменувати, перенести в теку, поділитися, видалити). PDF не дублюється — "
            "file_version той самий, що в оригіналу. Нотатки й прогрес у копії свої. Повтор "
            "безпечний: наявна копія повертається з 200."
        ),
        parameters=[PATH_ID],
        request=None,
        responses={
            201: BookSerializer,
            200: OpenApiResponse(BookSerializer, description="Копія вже була — та сама книга."),
            404: OpenApiResponse(ERROR, description="Книги нема в каталозі або вона й так ваша."),
        },
    )
    def post(self, request, pk):
        try:
            copy, created = add_to_library(request.user, pk)
        except Book.DoesNotExist:
            return error_response("Книги нема в каталозі", Code.NOT_FOUND, status.HTTP_404_NOT_FOUND)
        return Response(
            BookSerializer(copy).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _file_url(request, field):
    """JSON з адресою файлу замість 302: частина HTTP-клієнтів переносить Authorization на
    хост редиректу, і R2 відхиляє підписаний запит із чужим заголовком."""
    storage = field.storage
    expires_in = (
        storage.querystring_expire if getattr(storage, "querystring_auth", False) else None
    )
    return Response({"url": request.build_absolute_uri(field.url), "expires_in": expires_in})


class BookFileThrottle(UserRateThrottle):
    scope = "book_file"


class BookFileView(APIView):
    throttle_classes = [BookFileThrottle]

    @extend_schema(
        operation_id="getBookFile",
        summary="Адреса файлу книги",
        description=(
            "З R2 — підписане посилання на expires_in секунд: сам url не зберігати, а щоразу "
            "йти сюди. Кешувати завантажений PDF — за file_version."
        ),
        parameters=[PATH_ID],
        responses={
            200: docs.FileUrlSerializer,
            404: OpenApiResponse(ERROR, description="Книги нема або вона недоступна."),
            429: OpenApiResponse(ERROR, description="code=throttled: забагато запитів файлів."),
        },
    )
    def get(self, request, pk):
        book = get_object_or_404(visible_books(request.user), pk=pk)
        return _file_url(request, book.file)


class BookCoverView(APIView):

    @extend_schema(
        operation_id="getBookCover",
        summary="Адреса обкладинки книги",
        description=(
            "Як books/{id}/file/: url підписаний на expires_in секунд. Кешувати саму картинку "
            "за file_version — обкладинка рендериться з файлу."
        ),
        parameters=[PATH_ID],
        responses={
            200: docs.FileUrlSerializer,
            404: OpenApiResponse(
                ERROR, description="Книги нема, вона недоступна або без обкладинки (has_cover=false)."
            ),
        },
    )
    def get(self, request, pk):
        book = get_object_or_404(visible_books(request.user).exclude(cover=""), pk=pk)
        return _file_url(request, book.cover)


# ── Групи: вступ і вхідні запрошення ────────────────────────────────────────────
# Членство — лише через _join_group(): він заодно прибирає завислі запрошення. Нові книги
# й шари групи клієнт отримає наступним pull — окремої відповіді з вмістом нема.

JOINED = OpenApiResponse(
    JoinedGroupSerializer,
    description="Учасник групи. Далі — pull: книги й шари групи прийдуть у дельті.",
)


def _joined(group, joined):
    return Response(JoinedGroupSerializer({"group": group, "joined": joined}).data)


class GroupJoinView(APIView):
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="joinGroup",
        summary="Вступ у групу за посиланням",
        description="Повтор безпечний: уже учасник отримує 200 з joined=false.",
        request=JoinTokenSerializer,
        responses={
            200: JOINED,
            400: OpenApiResponse(
                ERROR, description="code=invalid (поля) або group_owner — користувач власник групи."
            ),
            404: OpenApiResponse(ERROR, description="Посилання недійсне або перевипущене."),
        },
    )
    def post(self, request):
        serializer = JoinTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = get_object_or_404(
            Group.objects.select_related("owner"), invite_token=serializer.validated_data["token"]
        )
        if group.owner_id == request.user.pk:
            return error_response(
                "Ви власник цієї групи", Code.GROUP_OWNER, status.HTTP_400_BAD_REQUEST
            )
        return _joined(group, _join_group(group, request.user))


@extend_schema_view(
    get=extend_schema(
        operation_id="listInvitations",
        summary="Вхідні запрошення в групи",
        description="Без пагінації. Запрошення прав не дає — доступ з'являється лише після accept.",
    )
)
class InvitationListView(ListAPIView):
    serializer_class = InvitationSerializer
    pagination_class = None

    def get_queryset(self):
        return (
            GroupInvitation.objects.filter(user=self.request.user)
            .select_related("group__owner", "invited_by")
            .order_by("-created_at", "-pk")
        )


def _own_invitation(request, pk):
    return get_object_or_404(
        GroupInvitation.objects.select_related("group__owner"), pk=pk, user=request.user
    )


INVITATION_NOT_FOUND = OpenApiResponse(
    ERROR, description="Запрошення нема: чуже, вже прийняте, відхилене чи скасоване."
)


class InvitationAcceptView(APIView):

    @extend_schema(
        operation_id="acceptInvitation",
        summary="Прийняти запрошення",
        request=None,
        parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH)],
        responses={200: JOINED, 404: INVITATION_NOT_FOUND},
    )
    def post(self, request, pk):
        group = _own_invitation(request, pk).group
        return _joined(group, _join_group(group, request.user))


class InvitationDeclineView(APIView):

    @extend_schema(
        operation_id="declineInvitation",
        summary="Відхилити запрошення",
        request=None,
        parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH)],
        responses={204: None, 404: INVITATION_NOT_FOUND},
    )
    def post(self, request, pk):
        _own_invitation(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Книги й теки: лише власні ───────────────────────────────────────────────────
# Змінювати може тільки власник, тому queryset — owner=request.user, а не visible_books():
# чужа видима книга тут так само 404, як і неіснуюча. Нове чи змінене клієнт бачить і
# наступним pull (updated_at), але відповідь уже містить об'єкт у форматі синку.

OWN_NOT_FOUND = OpenApiResponse(ERROR, description="Нема, чуже або недоступне.")
# Запас на multipart-обгортку й інші поля форми поверх самого файлу.
UPLOAD_OVERHEAD = 1024 * 1024


class BookUploadThrottle(UserRateThrottle):
    scope = "book_upload"


class BookCreateView(APIView):
    parser_classes = [MultiPartParser]
    throttle_classes = [BookUploadThrottle]

    @extend_schema(
        operation_id="uploadBook",
        summary="Завантажити PDF",
        description=(
            "multipart/form-data. Перевірки ті самі, що на сайті: розмір, сигнатура %PDF-, "
            "розбір, пароль, порожній документ. Обкладинка й кількість сторінок — одразу у відповіді."
        ),
        request=BookCreateSerializer,
        responses={
            201: BookSerializer,
            400: FIELD_ERRORS,
            413: OpenApiResponse(ERROR, description="Запит більший за ліміт файлу."),
            429: OpenApiResponse(ERROR, description="Забагато завантажень за годину."),
        },
    )
    def post(self, request):
        # До розбору тіла: інакше gunicorn спершу прийме весь файл, і лише потім
        # inspect_pdf() його відкине.
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > settings.MAX_UPLOAD_SIZE + UPLOAD_OVERHEAD:
            return error_response(
                "файл завеликий", Code.FILE_TOO_LARGE, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        serializer = BookCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        book = serializer.save()
        return Response(BookSerializer(book).data, status=status.HTTP_201_CREATED)


class BookDetailView(APIView):
    parser_classes = [JSONParser]

    def _own(self, request, pk):
        return get_object_or_404(Book.objects.filter(owner=request.user), pk=pk)

    @extend_schema(
        operation_id="updateBook",
        summary="Перейменувати чи перенести книгу",
        description="Файл тут не замінюється — лише назва й тека.",
        parameters=[PATH_ID],
        request=BookUpdateSerializer,
        responses={200: BookSerializer, 400: FIELD_ERRORS, 404: OWN_NOT_FOUND},
    )
    def patch(self, request, pk):
        book = self._own(request, pk)
        serializer = BookUpdateSerializer(
            book, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return Response(BookSerializer(serializer.save()).data)

    @extend_schema(
        operation_id="deleteBook",
        summary="Видалити книгу",
        description="Разом із шарами нотаток на ній; файл зі сховища прибирається після коміту.",
        parameters=[PATH_ID],
        responses={204: None, 404: OWN_NOT_FOUND},
    )
    def delete(self, request, pk):
        self._own(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FolderCreateView(APIView):
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="createFolder",
        summary="Створити теку",
        request=FolderWriteSerializer,
        responses={201: FolderSerializer, 400: FIELD_ERRORS},
    )
    def post(self, request):
        serializer = FolderWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        folder = serializer.save(owner=request.user)
        return Response(FolderSerializer(folder).data, status=status.HTTP_201_CREATED)


class FolderDetailView(APIView):
    parser_classes = [JSONParser]

    def _own(self, request, pk):
        return get_object_or_404(Folder.objects.filter(owner=request.user), pk=pk)

    @extend_schema(
        operation_id="updateFolder",
        summary="Перейменувати чи перенести теку",
        description="parent не може бути самою текою чи її підтекою.",
        parameters=[PATH_ID],
        request=FolderWriteSerializer,
        responses={200: FolderSerializer, 400: FIELD_ERRORS, 404: OWN_NOT_FOUND},
    )
    def patch(self, request, pk):
        folder = self._own(request, pk)
        serializer = FolderWriteSerializer(
            folder, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return Response(FolderSerializer(serializer.save()).data)

    @extend_schema(
        operation_id="deleteFolder",
        summary="Видалити теку",
        description=(
            "Разом із підтеками. Книги з них не видаляються, а переходять у корінь: "
            "наступний pull принесе їх із folder=null."
        ),
        parameters=[PATH_ID],
        responses={204: None, 404: OWN_NOT_FOUND},
    )
    def delete(self, request, pk):
        delete_folder(self._own(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Публікація власного шару для груп ───────────────────────────────────────────


class LayerPublicationsView(APIView):
    """Те саме, що форма публікації на сторінці книги (annotations.views.layer_publish_view)."""

    parser_classes = [JSONParser]

    def _state(self, request, book):
        layer = Layer.objects.filter(book=book, owner=request.user).first()
        groups = (
            layer.shared_with_groups.select_related("owner").order_by("name", "pk")
            if layer is not None
            else Group.objects.none()
        )
        publishable = publishable_groups(request.user, book).select_related("owner").order_by("name", "pk")
        return Response(
            LayerPublicationsSerializer({"groups": groups, "publishable_groups": publishable}).data
        )

    @extend_schema(
        operation_id="getLayerPublications",
        summary="Для яких груп відкрито мій шар на книзі",
        parameters=[OpenApiParameter("book_id", int, OpenApiParameter.PATH)],
        responses={200: LayerPublicationsSerializer, 404: OpenApiResponse(ERROR, description="Книга недоступна.")},
    )
    def get(self, request, book_id):
        return self._state(request, get_object_or_404(visible_books(request.user), pk=book_id))

    @extend_schema(
        operation_id="setLayerPublications",
        summary="Задати групи, для яких відкрито мій шар",
        description=(
            "Повна заміна набору. Учасники груп бачать шар лише для читання; нові "
            "публікації вони отримають наступним pull у shared_layers."
        ),
        parameters=[OpenApiParameter("book_id", int, OpenApiParameter.PATH)],
        request=LayerPublicationsUpdateSerializer,
        responses={
            200: LayerPublicationsSerializer,
            400: FIELD_ERRORS,
            404: OpenApiResponse(ERROR, description="Книга недоступна."),
        },
    )
    def put(self, request, book_id):
        book = get_object_or_404(visible_books(request.user), pk=book_id)
        serializer = LayerPublicationsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = set(serializer.validated_data["group_ids"])
        layer = Layer.objects.filter(book=book, owner=request.user).first()
        groups = list(publishable_groups(request.user, book).filter(pk__in=wanted))
    
        stale = set(layer.shared_with_groups.values_list("pk", flat=True)) if layer else set()
        if wanted - {group.pk for group in groups} - stale:
            raise ValidationError({"group_ids": ["Лише власні групи, учасники яких бачать цю книгу."]})
        if groups and layer is None:
            layer = Layer.objects.get_or_create(book=book, owner=request.user)[0]
        if layer is not None:
            # Повна заміна: знімаються й публікації для груп, що вже втратили доступ.
            layer.shared_with_groups.set(groups)
        return self._state(request, book)


# ── Share-посилання ─────────────────────────────────────────────────────────────
# Відкриваються лише на сайті (universal link): доступ за посиланням живе в сесії, а в
# JWT-клієнта її нема. Тут — керування власними посиланнями.


@extend_schema_view(
    get=extend_schema(
        operation_id="listShareLinks",
        summary="Мої share-посилання",
        description="Без пагінації. Прострочені теж у списку (is_expired).",
    ),
    post=extend_schema(
        operation_id="createShareLink",
        summary="Створити share-посилання",
        description="Відкривається на сайті за url; у застосунку доступу за ним нема.",
        responses={201: ShareLinkSerializer, 400: FIELD_ERRORS},
    ),
)
class ShareLinkListView(ListCreateAPIView):
    serializer_class = ShareLinkSerializer
    pagination_class = None
    parser_classes = [JSONParser]

    def get_queryset(self):
        return (
            ShareLink.objects.filter(created_by=self.request.user)
            .prefetch_related("note_groups")
            .order_by("-created_at", "-pk")
        )


class ShareLinkDetailView(APIView):

    @extend_schema(
        operation_id="deleteShareLink",
        summary="Відкликати share-посилання",
        description="Хто вже відкрив посилання на сайті, втрачає доступ з наступним запитом.",
        parameters=[PATH_ID],
        responses={204: None, 404: OWN_NOT_FOUND},
    )
    def delete(self, request, pk):
        get_object_or_404(ShareLink, pk=pk, created_by=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SyncThrottle(UserRateThrottle):
    scope = "sync"


SYNC_THROTTLED = OpenApiResponse(ERROR, description="code=throttled: забагато синків за хвилину.")


class SyncView(APIView):
    # Секції пушу вкладені — форма їх не передасть (раніше це був 400, тепер чесний 415),
    # і схема не обіцяє form/multipart.
    parser_classes = [JSONParser]
    throttle_classes = [SyncThrottle]

    @extend_schema(
        operation_id="pullSync",
        summary="Стан або дельта змін",
        description=(
            "Без since — повний стан. Клієнт зберігає server_time як наступний since і "
            "видаляє локально все, чого нема у visible_*_ids. Сервер сам відсуває since на "
            "кілька секунд назад, тож дублікати в дельті можливі — застосовувати за id."
        ),
        parameters=[
            OpenApiParameter(
                "since", OpenApiTypes.DATETIME,
                description="server_time з попереднього pull, ISO 8601 з часовим поясом.",
            ),
        ],
        responses={
            200: docs.SyncPullSerializer,
            400: OpenApiResponse(ERROR, description="code=invalid_since: без часового поясу чи битий."),
            429: SYNC_THROTTLED,
        },
    )
    def get(self, request):
        raw = request.query_params.get("since", "").strip()
        since = None
        if raw:
            try:
                since = parse_datetime(raw)
            except ValueError:  # формат правильний, а дата — ні
                since = None
            if since is None or since.tzinfo is None:
                return error_response(
                    "since: очікується ISO 8601 з часовим поясом",
                    Code.INVALID_SINCE,
                    status.HTTP_400_BAD_REQUEST,
                )
        return Response(pull_state(request.user, since))

    @extend_schema(
        operation_id="pushSync",
        summary="Надіслати зміни",
        description=docs.PUSH_DESCRIPTION,
        request=docs.SyncChangesSerializer,
        responses={
            200: docs.SyncPushSerializer,
            400: OpenApiResponse(
                ERROR,
                description="code=batch_invalid (битий формат секції) або batch_too_large.",
            ),
            429: SYNC_THROTTLED,
        },
    )
    def post(self, request):
        try:
            result = push_changes(request.user, request.data)
        except BatchError as error:
            return error_response(error, error.code, status.HTTP_400_BAD_REQUEST)
        return Response(result)


# ── Керування групами ───────────────────────────────────────────────────────────
# Змінює групу лише власник; учасник бачить склад і доступи, але не запрошення й посилання.
# Учасник не додається напряму: лише запрошення, яке він сам приймає (acceptInvitation),
# або вступ за посиланням (joinGroup). Нові й відкликані доступи учасники отримають
# наступним pull: visible_*_ids щоразу повні.


def _groups_of(user):
    """Власні групи й ті, де користувач учасник, з кількістю учасників."""
    return (
        Group.objects.filter(Q(owner=user) | Q(memberships__user=user))
        .distinct()
        .select_related("owner")
        .annotate(member_count=Count("memberships", distinct=True))
        .order_by("name", "pk")
    )


def _visible_group(request, pk):
    return get_object_or_404(_groups_of(request.user), pk=pk)


def _own_group(request, pk):
    return get_object_or_404(_groups_of(request.user).filter(owner=request.user), pk=pk)


def _group_response(request, group, status_code=status.HTTP_200_OK):
    # member_count — анотація: після create чи зміни перечитуємо, щоб він був у відповіді.
    group = _groups_of(request.user).get(pk=group.pk)
    return Response(
        GroupDetailSerializer(group, context={"request": request}).data, status=status_code
    )


GROUP_NOT_FOUND = OpenApiResponse(ERROR, description="Групи нема або користувач у ній не учасник.")
OWN_GROUP_NOT_FOUND = OpenApiResponse(
    ERROR, description="Групи нема або користувач не її власник (учасник теж отримує 404)."
)


@extend_schema_view(
    get=extend_schema(
        operation_id="listGroups",
        summary="Мої групи",
        description="Власні (is_owner=true) і ті, де користувач учасник. Без пагінації.",
    ),
    post=extend_schema(
        operation_id="createGroup",
        summary="Створити групу",
        description="Посилання для вступу (invite_url) вмикається одразу.",
        request=GroupWriteSerializer,
        responses={201: GroupDetailSerializer, 400: FIELD_ERRORS},
    ),
)
class GroupListView(ListCreateAPIView):
    serializer_class = GroupDetailSerializer
    pagination_class = None
    parser_classes = [JSONParser]

    def get_queryset(self):
        return _groups_of(self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = GroupWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = serializer.save(owner=request.user, invite_token=secrets.token_urlsafe(32))
        return _group_response(request, group, status.HTTP_201_CREATED)


class GroupDetailView(APIView):
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="getGroup",
        summary="Група",
        parameters=[PATH_ID],
        responses={200: GroupDetailSerializer, 404: GROUP_NOT_FOUND},
    )
    def get(self, request, pk):
        return _group_response(request, _visible_group(request, pk))

    @extend_schema(
        operation_id="updateGroup",
        summary="Перейменувати групу",
        parameters=[PATH_ID],
        request=GroupWriteSerializer,
        responses={200: GroupDetailSerializer, 400: FIELD_ERRORS, 404: OWN_GROUP_NOT_FOUND},
    )
    def patch(self, request, pk):
        group = _own_group(request, pk)
        serializer = GroupWriteSerializer(group, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        return _group_response(request, serializer.save())

    @extend_schema(
        operation_id="deleteGroup",
        summary="Видалити групу",
        description=(
            "Разом із членствами, запрошеннями, доступами й публікаціями шарів. Учасники "
            "втрачають доступ із наступним pull."
        ),
        parameters=[PATH_ID],
        responses={204: None, 404: OWN_GROUP_NOT_FOUND},
    )
    def delete(self, request, pk):
        _own_group(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupInviteLinkView(APIView):
    """Те саме, що кнопки «оновити» й «вимкнути» посилання на сторінці групи."""

    @extend_schema(
        operation_id="regenerateGroupInviteLink",
        summary="Новий токен посилання для вступу",
        description="Старе посилання перестає діяти. Вмикає вимкнене.",
        request=None,
        parameters=[PATH_ID],
        responses={200: GroupDetailSerializer, 404: OWN_GROUP_NOT_FOUND},
    )
    def post(self, request, pk):
        group = _own_group(request, pk)
        group.regenerate_invite_token()
        return _group_response(request, group)

    @extend_schema(
        operation_id="disableGroupInviteLink",
        summary="Вимкнути посилання для вступу",
        description="Запрошення за username діють і далі.",
        parameters=[PATH_ID],
        responses={200: GroupDetailSerializer, 404: OWN_GROUP_NOT_FOUND},
    )
    def delete(self, request, pk):
        group = _own_group(request, pk)
        group.invite_token = None
        group.save(update_fields=["invite_token"])
        return _group_response(request, group)


@extend_schema_view(
    get=extend_schema(
        operation_id="listGroupMembers",
        summary="Учасники групи",
        description="Без власника й без пагінації. Бачать власник і учасники.",
        parameters=[PATH_ID],
        responses={200: MemberSerializer(many=True), 404: GROUP_NOT_FOUND},
    )
)
class GroupMembersView(ListAPIView):
    serializer_class = MemberSerializer
    pagination_class = None

    def get_queryset(self):
        group = _visible_group(self.request, self.kwargs["pk"])
        return group.memberships.select_related("user").order_by("user__username", "pk")


class MembershipDetailView(APIView):

    @extend_schema(
        operation_id="deleteMembership",
        summary="Вилучити учасника або вийти з групи",
        description=(
            "Власник групи вилучає будь-кого; учасник може видалити лише власне членство. "
            "Доступ до матеріалів групи зникає з наступним pull."
        ),
        parameters=[PATH_ID],
        responses={204: None, 404: OpenApiResponse(ERROR, description="Членства нема або воно чуже.")},
    )
    def delete(self, request, pk):
        get_object_or_404(
            GroupMembership.objects.filter(Q(group__owner=request.user) | Q(user=request.user)),
            pk=pk,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupInvitationsView(APIView):
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="listGroupInvitations",
        summary="Надіслані запрошення групи",
        description="Ще не прийняті й не відхилені. Лише для власника, без пагінації.",
        parameters=[PATH_ID],
        responses={200: SentInvitationSerializer(many=True), 404: OWN_GROUP_NOT_FOUND},
    )
    def get(self, request, pk):
        group = _own_group(request, pk)
        invitations = group.invitations.select_related("user").order_by("-created_at", "-pk")
        return Response(SentInvitationSerializer(invitations, many=True).data)

    @extend_schema(
        operation_id="inviteToGroup",
        summary="Запросити користувача за username",
        description=(
            "Прав запрошення не дає: учасником користувач стане, лише прийнявши його "
            "(acceptInvitation)."
        ),
        parameters=[PATH_ID],
        request=InviteUserSerializer,
        responses={
            201: SentInvitationSerializer,
            400: OpenApiResponse(
                ERROR,
                description=(
                    "code=invalid (поля), user_not_found, group_owner (запрошує себе), "
                    "already_member, already_invited."
                ),
            ),
            404: OWN_GROUP_NOT_FOUND,
        },
    )
    def post(self, request, pk):
        group = _own_group(request, pk)
        serializer = InviteUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Ті самі перевірки, що InvitationForm на сайті.
        user = User.objects.filter(username=serializer.validated_data["username"]).first()
        bad_request = status.HTTP_400_BAD_REQUEST
        if user is None:
            return error_response("Користувача з таким username не знайдено.", Code.USER_NOT_FOUND, bad_request)
        if user.pk == group.owner_id:
            return error_response("Ви власник цієї групи.", Code.GROUP_OWNER, bad_request)
        if GroupMembership.objects.filter(group=group, user=user).exists():
            return error_response("Цей користувач уже в групі.", Code.ALREADY_MEMBER, bad_request)
        invitation, created = GroupInvitation.objects.get_or_create(
            group=group, user=user, defaults={"invited_by": request.user}
        )
        if not created:
            return error_response("Цього користувача вже запрошено.", Code.ALREADY_INVITED, bad_request)
        return Response(SentInvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class InvitationDetailView(APIView):

    @extend_schema(
        operation_id="cancelInvitation",
        summary="Скасувати надіслане запрошення",
        description="Лише власник групи. Запрошений відхиляє своє через declineInvitation.",
        parameters=[PATH_ID],
        responses={
            204: None,
            404: OpenApiResponse(ERROR, description="Запрошення нема або група не ваша."),
        },
    )
    def delete(self, request, pk):
        get_object_or_404(GroupInvitation, pk=pk, group__owner=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GroupAccessesView(APIView):
    parser_classes = [JSONParser]

    def _accesses(self, group):
        return group.accesses.select_related("book", "folder").order_by("-created_at", "-pk")

    @extend_schema(
        operation_id="listGroupAccesses",
        summary="Матеріали, відкриті групі",
        description="Бачать власник і учасники. Без пагінації.",
        parameters=[PATH_ID],
        responses={200: GroupAccessSerializer(many=True), 404: GROUP_NOT_FOUND},
    )
    def get(self, request, pk):
        group = _visible_group(request, pk)
        return Response(GroupAccessSerializer(self._accesses(group), many=True).data)

    @extend_schema(
        operation_id="grantGroupAccess",
        summary="Відкрити групі власну книгу чи теку",
        description="Тека — з усім вкладеним. Учасники отримають матеріал наступним pull.",
        parameters=[PATH_ID],
        request=GroupAccessSerializer,
        responses={201: GroupAccessSerializer, 400: FIELD_ERRORS, 404: OWN_GROUP_NOT_FOUND},
    )
    def post(self, request, pk):
        group = _own_group(request, pk)
        serializer = GroupAccessSerializer(
            data=request.data, context={"request": request, "group": group}
        )
        serializer.is_valid(raise_exception=True)
        try:
            # Savepoint: дубль, створений паралельним запитом між перевіркою й INSERT.
            with transaction.atomic():
                access = serializer.save(group=group, granted_by=request.user)
        except IntegrityError:
            raise ValidationError("Група вже має доступ до цього матеріалу.") from None
        return Response(GroupAccessSerializer(access).data, status=status.HTTP_201_CREATED)


class AccessDetailView(APIView):

    @extend_schema(
        operation_id="revokeGroupAccess",
        summary="Закрити групі доступ",
        description="Учасники втрачають матеріал із наступним pull (visible_*_ids).",
        parameters=[PATH_ID],
        responses={204: None, 404: OpenApiResponse(ERROR, description="Доступу нема або група не ваша.")},
    )
    def delete(self, request, pk):
        get_object_or_404(GroupAccess, pk=pk, group__owner=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
