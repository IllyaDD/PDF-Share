from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Group, GroupAccess, GroupInvitation, GroupMembership, ShareLink


class GroupMembershipInline(TabularInline):
    model = GroupMembership
    extra = 0
    autocomplete_fields = ("user",)
    readonly_fields = ("joined_at",)


class GroupInvitationInline(TabularInline):
    model = GroupInvitation
    extra = 0
    autocomplete_fields = ("user", "invited_by")
    readonly_fields = ("created_at",)


class GroupAccessInline(TabularInline):
    model = GroupAccess
    extra = 0
    autocomplete_fields = ("book", "folder", "granted_by")
    readonly_fields = ("created_at",)


@admin.register(Group)
class GroupAdmin(ModelAdmin):
    list_display = ("name", "owner", "member_count", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    autocomplete_fields = ("owner",)
    readonly_fields = ("invite_token", "created_at")
    inlines = (GroupMembershipInline, GroupInvitationInline, GroupAccessInline)

    @display(description="Members")
    def member_count(self, group):
        return group.memberships.count()


@admin.register(ShareLink)
class ShareLinkAdmin(ModelAdmin):
    list_display = (
        "token",
        "book",
        "folder",
        "created_by",
        "include_annotations",
        "expires_at",
        "status",
        "created_at",
    )
    list_filter = ("include_annotations", "expires_at", "created_at")
    search_fields = ("token", "book__title", "folder__name", "created_by__username")
    autocomplete_fields = ("book", "folder", "created_by")
    # Теми й режим задає лише форма посилання (вона перевіряє книгу й власника тем);
    # в адмінці — тільки перегляд, щоб не зібрати SELECTED-посилання з чужими темами.
    exclude = ("note_groups",)
    readonly_fields = ("token", "annotation_scope", "topics", "created_at")
    date_hierarchy = "created_at"

    @display(
        description="Status",
        label={"Active": "success", "Expired": "danger"},
    )
    def status(self, link):
        return "Expired" if link.is_expired else "Active"

    @display(description="Topics")
    def topics(self, link):
        groups = link.note_groups.select_related("layer__book")
        return ", ".join(f"{group.layer.book.title} — {group.name}" for group in groups) or "—"
