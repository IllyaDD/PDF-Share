"""Згода на кукі: «Прийняти всі» або «Лише обов'язкові».

Зараз сайт ставить лише обов'язкові кукі (сесія, CSRF, мова) і зберігає в localStorage
налаштування, які користувач обрав сам (тема, тема нотаток у читалці) — вони згоди не
потребують. Вибір лежить у кукі ``cookie_consent`` і потрібен для майбутніх необов'язкових
скриптів (аналітика): у шаблоні — ``{% if cookie_consent == "all" %}``, у JS —
``cookieConsent.allows("analytics")`` з ``static/js/cookie-consent.js``.
"""

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

CONSENT_COOKIE = "cookie_consent"
CONSENT_ALL = "all"
CONSENT_NECESSARY = "necessary"
CONSENT_CHOICES = (CONSENT_ALL, CONSENT_NECESSARY)
# Рік: європейські регулятори радять перепитувати не рідше ніж раз на 6–13 місяців.
CONSENT_MAX_AGE = 60 * 60 * 24 * 365


def get_consent(request):
    """Вибір користувача або ``None``, якщо його ще нема чи кукі підроблене."""
    value = request.COOKIES.get(CONSENT_COOKIE)
    return value if value in CONSENT_CHOICES else None


def cookie_consent(request):
    """Контекст-процесор: ``cookie_consent`` — ``"all"``, ``"necessary"`` або ``None``."""
    return {"cookie_consent": get_consent(request)}


@require_POST
def set_cookie_consent(request):
    """Записує вибір у кукі. Форма банера працює й без JS: тоді редирект на ``next``.

    ``cookie-consent.js`` шле ту саму форму через fetch із заголовком
    ``X-Requested-With: fetch`` і отримує 204 замість редиректу.
    """
    choice = request.POST.get("consent")
    if choice not in CONSENT_CHOICES:
        return HttpResponseBadRequest()

    if request.headers.get("X-Requested-With") == "fetch":
        response = HttpResponse(status=204)
    else:
        # Редирект лише в гілці, де перевірка пройшла, — цю форму розпізнає CodeQL.
        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            response = redirect(next_url)
        else:
            response = redirect("book_library:home")

    # Не HttpOnly навмисно: необов'язкові скрипти читають вибір у браузері.
    response.set_cookie(
        CONSENT_COOKIE,
        choice,
        max_age=CONSENT_MAX_AGE,
        samesite="Lax",
        secure=not settings.DEBUG,
    )
    return response
