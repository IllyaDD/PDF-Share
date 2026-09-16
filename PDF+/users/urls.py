from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_view, name="register"),
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),

    path("google/", views.GoogleLoginView.as_view(), name="google_login"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google_callback"),

    path("profile/", views.profile_view, name="profile"),
    path("profile/edit/", views.edit_profile_view, name="edit_profile"),
    path("profile/delete/", views.delete_profile_view, name="delete_profile"),
]
