from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Group, GroupAccess, GroupMembership, ShareLink


class GroupMembershipInline(TabularInline):
    model = GroupMembership
    extra = 0
    autocomplete_fields = ("user",)
    readonly_fields = ("joined_at",)


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
    inlines = (GroupMembershipInline, GroupAccessInline)

    @display(description="Members")
    def member_count(self, group):
        return group.memberships.count()


@admin.register(ShareLink)
class ShareLinkAdmin(ModelAdmin):
    list_display = ("token", "book", "folder", "created_by", "expires_at", "status", "created_at")
    list_filter = ("expires_at", "created_at")
    search_fields = ("token", "book__title", "folder__name", "created_by__username")
    autocomplete_fields = ("book", "folder", "created_by")
    readonly_fields = ("token", "created_at")
    date_hierarchy = "created_at"

    @display(
        description="Status",
        label={"Active": "success", "Expired": "danger"},
    )
    def status(self, link):
        return "Expired" if link.is_expired else "Active"
