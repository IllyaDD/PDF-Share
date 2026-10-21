from book_library.access import catalog_folder_ids, visible_books
from book_library.models import Book
from book_library.views import _search_titles
from django.shortcuts import get_object_or_404, redirect
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .serializers import CatalogBookSerializer
from .sync import BatchError, pull_state, push_changes


class AuthTokenThrottle(AnonRateThrottle):
    scope = "auth_token"


class TokenPairSerializer(TokenObtainPairSerializer):

    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data["user"] = {"id": user.pk, "username": user.username}
        return data


class ObtainTokenView(TokenObtainPairView):

    serializer_class = TokenPairSerializer
    throttle_classes = [AuthTokenThrottle]


class RefreshTokenView(TokenRefreshView):
    throttle_classes = [AuthTokenThrottle]


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
