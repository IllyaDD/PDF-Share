from django.urls import path

from . import views

app_name = "book_library"

urlpatterns = [
    path("", views.home_view, name="home"),

    path("library/", views.library_view, name="library"),
    path("library/folder/<int:pk>/", views.library_view, name="folder"),
    path("shared/", views.shared_with_me_view, name="shared_with_me"),

    path("folders/new/", views.folder_create_view, name="folder_create"),
    path("folders/<int:pk>/edit/", views.folder_edit_view, name="folder_edit"),
    path("folders/<int:pk>/delete/", views.folder_delete_view, name="folder_delete"),
    path("folders/<int:pk>/add-books/", views.folder_add_books_view, name="folder_add_books"),

    path("books/upload/", views.book_upload_view, name="book_upload"),
    path("books/<int:pk>/", views.book_detail_view, name="book_detail"),
    path("books/<int:pk>/read/", views.book_reader_view, name="book_reader"),
    path("books/<int:pk>/edit/", views.book_edit_view, name="book_edit"),
    path("books/<int:pk>/delete/", views.book_delete_view, name="book_delete"),
    path("books/<int:pk>/move/", views.book_move_view, name="book_move"),
]
