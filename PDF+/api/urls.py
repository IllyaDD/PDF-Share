from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("auth/token/", views.ObtainTokenView.as_view(), name="auth_token"),
    path("auth/token/refresh/", views.RefreshTokenView.as_view(), name="auth_token_refresh"),
    path("catalog/books/", views.CatalogBooksView.as_view(), name="catalog_books"),
    path("books/<int:pk>/file/", views.BookFileView.as_view(), name="book_file"),
    path("sync/", views.SyncView.as_view(), name="sync"),
]
