from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group as AuthGroup
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User

# Django-вські групи прав не використовуються (адмінка лише для суперюзера)
# і плутаються з sharing.Group у меню.
admin.site.unregister(AuthGroup)


@admin.register(User)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = ("username", "email", "role", "is_staff", "date_joined")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("-date_joined",)
    fieldsets = UserAdmin.fieldsets + (("Роль", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Роль", {"fields": ("email", "role")}),)
