from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from . import views

app_name = "api"

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="docs"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),
    path("auth/token/", views.ObtainTokenView.as_view(), name="auth_token"),
    path("auth/token/refresh/", views.RefreshTokenView.as_view(), name="auth_token_refresh"),
    path("auth/google/", views.GoogleTokenView.as_view(), name="auth_google"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth_logout"),
    path("auth/logout/all/", views.LogoutAllView.as_view(), name="auth_logout_all"),
    path("me/", views.MeView.as_view(), name="me"),
    path("catalog/books/", views.CatalogBooksView.as_view(), name="catalog_books"),
    path(
        "catalog/books/<int:pk>/add-to-library/",
        views.CatalogBookAddView.as_view(),
        name="catalog_book_add",
    ),
    path("books/", views.BookCreateView.as_view(), name="books"),
    path("books/<int:pk>/", views.BookDetailView.as_view(), name="book_detail"),
    path("books/<int:pk>/file/", views.BookFileView.as_view(), name="book_file"),
    path("books/<int:pk>/cover/", views.BookCoverView.as_view(), name="book_cover"),
    path("folders/", views.FolderCreateView.as_view(), name="folders"),
    path("folders/<int:pk>/", views.FolderDetailView.as_view(), name="folder_detail"),
    path(
        "layers/<int:book_id>/publications/",
        views.LayerPublicationsView.as_view(),
        name="layer_publications",
    ),
    path("share-links/", views.ShareLinkListView.as_view(), name="share_links"),
    path("share-links/<int:pk>/", views.ShareLinkDetailView.as_view(), name="share_link_detail"),
    path("groups/", views.GroupListView.as_view(), name="groups"),
    path("groups/join/", views.GroupJoinView.as_view(), name="group_join"),
    path("groups/<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path(
        "groups/<int:pk>/invite-link/",
        views.GroupInviteLinkView.as_view(),
        name="group_invite_link",
    ),
    path("groups/<int:pk>/members/", views.GroupMembersView.as_view(), name="group_members"),
    path(
        "groups/<int:pk>/invitations/",
        views.GroupInvitationsView.as_view(),
        name="group_invitations",
    ),
    path("groups/<int:pk>/accesses/", views.GroupAccessesView.as_view(), name="group_accesses"),
    path("memberships/<int:pk>/", views.MembershipDetailView.as_view(), name="membership_detail"),
    path("accesses/<int:pk>/", views.AccessDetailView.as_view(), name="access_detail"),
    path("invitations/", views.InvitationListView.as_view(), name="invitations"),
    path("invitations/<int:pk>/", views.InvitationDetailView.as_view(), name="invitation_detail"),
    path(
        "invitations/<int:pk>/accept/",
        views.InvitationAcceptView.as_view(),
        name="invitation_accept",
    ),
    path(
        "invitations/<int:pk>/decline/",
        views.InvitationDeclineView.as_view(),
        name="invitation_decline",
    ),
    path("sync/", views.SyncView.as_view(), name="sync"),
]
