import ipaddress

from book_library.access import catalog_folder_ids, visible_books
from book_library.models import Book
from book_library.views import _search_titles
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
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
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.generics import ListAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
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
from sharing.models import Group, GroupInvitation
from sharing.views import _join_group
from users.google import UnverifiedEmail, google_account
from users.models import User

from . import schema as docs
from .serializers import (
    CatalogBookSerializer,
    InvitationSerializer,
    JoinedGroupSerializer,
    JoinTokenSerializer,
    MeSerializer,
)
from .sync import BatchError, pull_state, push_changes

FIELD_ERRORS = OpenApiResponse(OpenApiTypes.OBJECT, description="Помилки полів: {поле: [повідомлення]}.")
DETAIL = docs.DetailSerializer
THROTTLED = OpenApiResponse(DETAIL, description="Понад 10 запитів на хвилину з однієї IP.")


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
            401: OpenApiResponse(DETAIL, description="Невірний логін чи пароль або акаунт вимкнено."),
            429: THROTTLED,
        },
    )
)
class ObtainTokenView(TokenObtainPairView):

    serializer_class = TokenPairSerializer
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
                DETAIL, description="Refresh недійсний, прострочений, відкликаний або акаунта нема."
            ),
            429: THROTTLED,
        },
    )
)
class RefreshTokenView(TokenRefreshView):
    serializer_class = RefreshSerializer
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
            401: OpenApiResponse(DETAIL, description="Refresh недійсний або вже відкликаний."),
        },
    )
)
class LogoutView(TokenBlacklistView):
    """Вихід з пристрою: його refresh іде в blacklist.

    Автентифікація — сам refresh, а не access: той міг уже прострочитись. Виданий access
    живе до кінця свого строку (до 30 хв), бо перевіряється лише підписом.
    """


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
            400: OpenApiResponse(docs.ErrorSerializer, description="Нема id_token."),
            401: OpenApiResponse(docs.ErrorSerializer, description="Токен недійсний або прострочений."),
            403: OpenApiResponse(
                docs.ErrorSerializer, description="Google не підтвердив пошту або акаунт вимкнено."
            ),
            429: THROTTLED,
            503: OpenApiResponse(
                docs.ErrorSerializer, description="Google недоступний або вхід не налаштований."
            ),
        },
    )
    def post(self, request):
        raw = request.data.get("id_token")
        if not isinstance(raw, str) or not raw:
            return Response({"error": "id_token: очікується рядок"}, status=status.HTTP_400_BAD_REQUEST)
        audiences = settings.GOOGLE_TOKEN_AUDIENCES
        # Без aud бібліотека не перевіряє отримувача, і токен будь-якого чужого
        # Google-застосунку відкрив би акаунт. Тому порожній список — відмова, а не пропуск.
        if not audiences:
            return Response(
                {"error": "вхід через Google не налаштований"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            # Підпис ключами Google, iss, exp і aud перевіряє бібліотека.
            claims = id_token.verify_oauth2_token(raw, google_requests.Request(), audience=audiences)
        except TransportError:
            return Response(
                {"error": "Google тимчасово недоступний"}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except (ValueError, GoogleAuthError):
            return Response(
                {"error": "id_token: недійсний або прострочений"}, status=status.HTTP_401_UNAUTHORIZED
            )
        try:
            user, created, _reset = google_account(claims)
        except UnverifiedEmail:
            return Response(
                {"error": "Google не підтвердив адресу пошти"}, status=status.HTTP_403_FORBIDDEN
            )
        if not user.is_active:
            return Response({"error": "акаунт вимкнено"}, status=status.HTTP_403_FORBIDDEN)

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


class BookFileView(APIView):

    @extend_schema(
        operation_id="getBookFile",
        summary="Файл книги",
        description=(
            "302 на файл у сховищі. З R2 — підписане посилання, що живе 3600 с: не кешувати "
            "його, а щоразу йти сюди. Заголовок Authorization на інший хост не передавати."
        ),
        parameters=[
            OpenApiParameter("id", int, OpenApiParameter.PATH),
            OpenApiParameter(
                "Location", str, OpenApiParameter.HEADER, response=[302],
                description="URL PDF-файлу.",
            ),
        ],
        responses={
            302: OpenApiResponse(description="Редирект на PDF."),
            404: OpenApiResponse(DETAIL, description="Книги нема або вона недоступна."),
        },
    )
    def get(self, request, pk):
        book = get_object_or_404(visible_books(request.user), pk=pk)
        return redirect(book.file.url)


class BookCoverView(APIView):

    @extend_schema(
        operation_id="getBookCover",
        summary="Обкладинка книги",
        description=(
            "302 на JPEG у сховищі, як books/{id}/file/: підписане посилання не кешувати. "
            "Кешувати саму картинку за file_version — обкладинка рендериться з файлу."
        ),
        parameters=[
            OpenApiParameter("id", int, OpenApiParameter.PATH),
            OpenApiParameter(
                "Location", str, OpenApiParameter.HEADER, response=[302],
                description="URL JPEG-обкладинки.",
            ),
        ],
        responses={
            302: OpenApiResponse(description="Редирект на JPEG."),
            404: OpenApiResponse(
                DETAIL, description="Книги нема, вона недоступна або без обкладинки (has_cover=false)."
            ),
        },
    )
    def get(self, request, pk):
        book = get_object_or_404(visible_books(request.user).exclude(cover=""), pk=pk)
        return redirect(book.cover.url)


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

    @extend_schema(
        operation_id="joinGroup",
        summary="Вступ у групу за посиланням",
        description="Повтор безпечний: уже учасник отримує 200 з joined=false.",
        request=JoinTokenSerializer,
        responses={
            200: JOINED,
            400: OpenApiResponse(
                OpenApiTypes.OBJECT,
                description="Помилки полів або {error}, якщо користувач — власник групи.",
            ),
            404: OpenApiResponse(DETAIL, description="Посилання недійсне або перевипущене."),
        },
    )
    def post(self, request):
        serializer = JoinTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = get_object_or_404(
            Group.objects.select_related("owner"), invite_token=serializer.validated_data["token"]
        )
        if group.owner_id == request.user.pk:
            return Response({"error": "Ви власник цієї групи"}, status=status.HTTP_400_BAD_REQUEST)
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
    DETAIL, description="Запрошення нема: чуже, вже прийняте, відхилене чи скасоване."
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


class SyncView(APIView):
    # Секції пушу вкладені — форма їх не передасть (раніше це був 400, тепер чесний 415),
    # і схема не обіцяє form/multipart.
    parser_classes = [JSONParser]

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
            400: OpenApiResponse(docs.ErrorSerializer, description="since без часового поясу чи битий."),
        },
    )
    def get(self, request):
        raw = request.query_params.get("since", "").strip()
        since = None
        if raw:
            since = parse_datetime(raw)
            if since is None or since.tzinfo is None:
                return Response(
                    {"error": "since: очікується ISO 8601 з часовим поясом"},
                    status=status.HTTP_400_BAD_REQUEST,
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
                docs.ErrorSerializer, description="Битий формат секції або забагато операцій."
            ),
        },
    )
    def post(self, request):
        try:
            result = push_changes(request.user, request.data)
        except BatchError as error:
            return Response({"error": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
