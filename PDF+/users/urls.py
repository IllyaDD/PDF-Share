from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),

    path("password/reset/", views.ResetRequestView.as_view(), name="password_reset"),
    path("password/reset/done/", views.ResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password/reset/<uidb64>/<token>/",
        views.ResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/complete/",
        views.ResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("password/change/", views.ChangePasswordView.as_view(), name="password_change"),

    path("google/", views.GoogleLoginView.as_view(), name="google_login"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google_callback"),

    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.edit_profile_view, name="edit_profile"),
    path("profile/delete/", views.delete_profile_view, name="delete_profile"),
]
