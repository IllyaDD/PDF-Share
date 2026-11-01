from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("auth/token/", views.ObtainTokenView.as_view(), name="auth_token"),
    path("auth/token/refresh/", views.RefreshTokenView.as_view(), name="auth_token_refresh"),
    path("auth/google/", views.GoogleTokenView.as_view(), name="auth_google"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth_logout"),
    path("auth/logout/all/", views.LogoutAllView.as_view(), name="auth_logout_all"),
    path("me/", views.MeView.as_view(), name="me"),
    path("catalog/books/", views.CatalogBooksView.as_view(), name="catalog_books"),
    path("books/<int:pk>/file/", views.BookFileView.as_view(), name="book_file"),
    path("sync/", views.SyncView.as_view(), name="sync"),
]
