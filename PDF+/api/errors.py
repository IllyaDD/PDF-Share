"""Єдиний формат помилок /api/v1/: {"error": текст, "code": машинний код[, "fields": {...}]}.

Клієнт розбирає лише code: тексти українською й можуть змінюватись. Помилки полів
серіалізаторів лежать у fields — {поле: [повідомлення]}, як їх віддає DRF.

Обробник підключений глобально (REST_FRAMEWORK.EXCEPTION_HANDLER), але переписує лише
відповіді застосунку api: API читалки (annotations/api.py) має власний формат {"error"},
який читає reader.js.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler


class Code:
    INVALID = "invalid" 
    NOT_FOUND = "not_found"
    FILE_TOO_LARGE = "file_too_large"
    GROUP_OWNER = "group_owner"
    USER_NOT_FOUND = "user_not_found"
    ALREADY_MEMBER = "already_member"
    ALREADY_INVITED = "already_invited"
    INVALID_SINCE = "invalid_since"
    BATCH_INVALID = "batch_invalid"
    BATCH_TOO_LARGE = "batch_too_large"
    GOOGLE_TOKEN_MISSING = "google_token_missing"
    GOOGLE_TOKEN_INVALID = "google_token_invalid"
    GOOGLE_EMAIL_UNVERIFIED = "google_email_unverified"
    GOOGLE_NOT_CONFIGURED = "google_not_configured"
    GOOGLE_UNAVAILABLE = "google_unavailable"
    ACCOUNT_DISABLED = "account_disabled"


def error_response(message, code, status, fields=None):
    body = {"error": str(message), "code": code}
    if fields is not None:
        body["fields"] = fields
    return Response(body, status=status)


def _first_message(data):
    """Перше повідомлення з вкладених помилок полів — для людського поля error."""
    if isinstance(data, dict):
        for value in data.values():
            message = _first_message(value)
            if message:
                return message
    elif isinstance(data, list):
        for value in data:
            message = _first_message(value)
            if message:
                return message
    elif data:
        return str(data)
    return ""


def _body(exc, data):
    if isinstance(exc, exceptions.ValidationError):
        fields = data if isinstance(data, dict) else {"non_field_errors": data}
        return {
            "error": _first_message(fields) or "Некоректні дані.",
            "code": Code.INVALID,
            "fields": fields,
        }
    if isinstance(data, dict):
        # SimpleJWT (InvalidToken) кладе в detail словник {detail, code, messages}.
        return {
            "error": str(data.get("detail", "")),
            "code": str(data.get("code") or exc.default_code),
        }
    codes = exc.get_codes()
    return {"error": str(data), "code": codes if isinstance(codes, str) else exc.default_code}


def _is_api(context):
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    return match is not None and match.app_name == "api"


def api_exception_handler(exc, context):
    if not _is_api(context):
        return exception_handler(exc, context)
    # Те саме, що робить exception_handler DRF, але до нього: потрібен сам APIException.
    # Текст Http404 з get_object_or_404 («No Book matches…») назовні не йде.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    response = exception_handler(exc, context)
    if response is not None:
        response.data = _body(exc, exc.detail)
    return response
