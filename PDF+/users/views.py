from random import randint

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View

from book_sharing_settings.oauth import oauth

from .forms import (
    ChangePasswordForm,
    LoginForm,
    ProfileForm,
    RegistrationForm,
    ResetRequestForm,
    ResetSetPasswordForm,
)
from .models import User

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _unique_username(base):
    username = base
    while User.objects.filter(username=username).exists():
        username = f"{base}{randint(1, 9999)}"
    return username


def register_view(request):
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend=MODEL_BACKEND)

        # Редирект лише всередині гілки, де перевірка пройшла: так само, як у _safe_next(),
        # і саме цю форму розпізнає CodeQL (перезапис змінної в гілці «не пройшла» — ні).
        next_url = request.GET.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            response = redirect(next_url)
        else:
            response = redirect("book_library:welcome")
        response.set_cookie(
            key="registration_success",
            value="1",
            max_age=60 * 60 * 24,
            httponly=True,
            samesite="Lax",
            secure=not settings.DEBUG,
        )
        messages.success(request, _("Реєстрація успішна!"))
        return response

    return render(request, "users/register.html", {"form": form})


class UserLoginView(LoginView):
    template_name = "users/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


# Вбудовані в'юхи Django редиректять на URL без неймспейсу ("password_reset_done"),
# а наші живуть під "accounts:", тому success_url перевизначено в кожній.
class ResetRequestView(PasswordResetView):
    template_name = "users/password_reset_form.html"
    form_class = ResetRequestForm
    email_template_name = "users/emails/password_reset.txt"
    subject_template_name = "users/emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")


class ResetDoneView(PasswordResetDoneView):
    template_name = "users/password_reset_done.html"


class ResetConfirmView(PasswordResetConfirmView):
    template_name = "users/password_reset_confirm.html"
    form_class = ResetSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class ResetCompleteView(PasswordResetCompleteView):
    template_name = "users/password_reset_complete.html"


class ChangePasswordView(LoginRequiredMixin, PasswordChangeView):
    template_name = "users/password_change_form.html"
    form_class = ChangePasswordForm
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, _("Пароль змінено."))
        return super().form_valid(form)


def logout_view(request):
    if request.method == "POST":
        logout(request)
        messages.success(request, _("Ви вийшли з акаунта."))
    return redirect("book_library:home")


class GoogleLoginView(View):
    def get(self, request):
        redirect_url = request.build_absolute_uri(reverse("accounts:google_callback"))
        return oauth.google.authorize_redirect(request, redirect_url)


class GoogleCallbackView(View):
    def get(self, request):
        token = oauth.google.authorize_access_token(request)
        user_info = oauth.google.get(
            "https://openidconnect.googleapis.com/v1/userinfo", token=token
        ).json()

        email = user_info["email"]
        base_name = (
            user_info.get("given_name")
            or user_info.get("name")
            or email.split("@")[0]
        )

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": _unique_username(base_name),
                "first_name": user_info.get("given_name", ""),
                "last_name": user_info.get("family_name", ""),
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        login(request, user, backend=MODEL_BACKEND)

        response = redirect("book_library:welcome" if created else "book_library:home")
        response.set_cookie(
            key="google_registration_success",
            value="1",
            max_age=60 * 60 * 24,
            httponly=True,
            samesite="Lax",
            secure=not settings.DEBUG,
        )
        messages.success(
            request,
            _("Реєстрація успішна!") if created else _("Вітаємо знову!"),
        )
        return response


@login_required
def profile_view(request):
    return render(request, "users/profile.html")


@login_required
def edit_profile_view(request):
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Профіль успішно оновлено!"))
        return redirect("accounts:profile")
    return render(request, "users/edit_profile.html", {"form": form})


@login_required
def delete_profile_view(request):
    if request.method == "POST":
        request.user.delete()
        messages.success(request, _("Акаунт видалено."))
        return redirect("book_library:home")
    return redirect("accounts:profile")
