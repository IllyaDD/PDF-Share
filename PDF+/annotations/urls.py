from django.urls import path

from . import api, views

app_name = "annotations"

urlpatterns = [
    path("books/<int:book_pk>/annotations/", views.book_annotations_view, name="book_annotations"),
    path("annotations/<int:pk>/delete/", views.annotation_delete_view, name="annotation_delete"),
    path("note-groups/<int:pk>/rename/", views.note_group_rename_view, name="note_group_rename"),
    path("note-groups/<int:pk>/delete/", views.note_group_delete_view, name="note_group_delete"),
    path("books/<int:book_pk>/annotations/publish/", views.layer_publish_view, name="layer_publish"),

    path("api/books/<int:book_pk>/annotations/", api.book_annotations, name="api_book_annotations"),
    path("api/books/<int:book_pk>/shared-layers/", api.book_shared_layers, name="api_book_shared_layers"),
    path("api/annotations/<int:pk>/", api.annotation_detail, name="api_annotation_detail"),
    path("api/books/<int:book_pk>/note-groups/", api.book_note_groups, name="api_book_note_groups"),
    path("api/note-groups/<int:pk>/", api.note_group_detail, name="api_note_group_detail"),
]
