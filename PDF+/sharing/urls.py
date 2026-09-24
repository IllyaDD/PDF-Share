from django.urls import path

from . import views

app_name = "sharing"

urlpatterns = [
    path("groups/", views.group_list_view, name="group_list"),
    path("groups/new/", views.group_create_view, name="group_create"),
    path("groups/<int:pk>/", views.group_detail_view, name="group_detail"),
    path("groups/<int:pk>/edit/", views.group_edit_view, name="group_edit"),
    path("groups/<int:pk>/delete/", views.group_delete_view, name="group_delete"),
    path("groups/<int:pk>/invite/", views.group_invite_view, name="group_invite"),
    path("groups/join/<str:token>/", views.group_join_view, name="group_join"),
    path("memberships/<int:pk>/remove/", views.membership_remove_view, name="membership_remove"),

    path("access/new/", views.access_create_view, name="access_create"),
    path("access/<int:pk>/delete/", views.access_delete_view, name="access_delete"),

    path("links/", views.link_list_view, name="link_list"),
    path("links/new/", views.link_create_view, name="link_create"),
    path("links/<int:pk>/delete/", views.link_delete_view, name="link_delete"),
    path("s/<str:token>/", views.share_view, name="share_view"),
]
