from random import randint

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from book_sharing_settings.oauth import oauth

from .forms import LoginForm, ProfileForm, RegistrationForm
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

        next_url = request.GET.get("next", "")
        if not url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            next_url = reverse("book_library:home")

        response = redirect(next_url)
        response.set_cookie(
            key="registration_success",
            value="1",
            max_age=60 * 60 * 24,
            httponly=True,
            samesite="Lax",
            secure=not settings.DEBUG,
        )
        messages.success(request, "Реєстрація успішна!")
        return response

    return render(request, "users/register.html", {"form": form})


class UserLoginView(LoginView):
    template_name = "users/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def logout_view(request):
    if request.method == "POST":
        logout(request)
        messages.success(request, "Ви вийшли з акаунта.")
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

        response = redirect("book_library:home")
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
            "Реєстрація успішна!" if created else "Вітаємо знову!",
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
        messages.success(request, "Профіль успішно оновлено!")
        return redirect("accounts:profile")
    return render(request, "users/edit_profile.html", {"form": form})


@login_required
def delete_profile_view(request):
    if request.method == "POST":
        request.user.delete()
        messages.success(request, "Акаунт видалено.")
        return redirect("book_library:home")
    return redirect("accounts:profile")
