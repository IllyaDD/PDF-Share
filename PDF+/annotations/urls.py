from django.urls import path

from . import api, views

app_name = "annotations"

urlpatterns = [
    path("books/<int:book_pk>/annotations/", views.book_annotations_view, name="book_annotations"),
    path("annotations/<int:pk>/delete/", views.annotation_delete_view, name="annotation_delete"),

    path("api/books/<int:book_pk>/annotations/", api.book_annotations, name="api_book_annotations"),
    path("api/annotations/<int:pk>/", api.annotation_detail, name="api_annotation_detail"),
]
