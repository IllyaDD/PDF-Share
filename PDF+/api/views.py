import ipaddress

from book_library.access import catalog_folder_ids, visible_books
from book_library.models import Book
from book_library.views import _search_titles
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.generics import ListAPIView
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
from users.google import UnverifiedEmail, google_account
from users.models import User

from .serializers import CatalogBookSerializer, MeSerializer
from .sync import BatchError, pull_state, push_changes


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


class RefreshTokenView(TokenRefreshView):
    serializer_class = RefreshSerializer
    throttle_classes = [AuthTokenThrottle]


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

    def post(self, request):
        revoke_refresh_tokens(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):

    def get(self, request):
        return Response(MeSerializer(request.user).data)

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

    def get(self, request, pk):
        book = get_object_or_404(visible_books(request.user), pk=pk)
        return redirect(book.file.url)


class SyncView(APIView):
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

    def post(self, request):
        try:
            result = push_changes(request.user, request.data)
        except BatchError as error:
            return Response({"error": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
