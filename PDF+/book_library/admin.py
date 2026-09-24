from django.contrib import admin
from django.template.defaultfilters import filesizeformat
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from annotations.models import Layer

from .forms import BookAdminForm
from .models import Book, Folder


class LayerInline(TabularInline):

    model = Layer
    extra = 0
    can_delete = False
    fields = ("owner", "annotation_count", "created_at")
    readonly_fields = ("owner", "annotation_count", "created_at")
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="Annotations")
    def annotation_count(self, layer):
        return layer.annotations.count()


@admin.register(Folder)
class FolderAdmin(ModelAdmin):
    list_display = ("name", "owner", "parent", "is_catalog", "created_at")
    list_filter = ("is_catalog", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    autocomplete_fields = ("owner", "parent")
    date_hierarchy = "created_at"


@admin.register(Book)
class BookAdmin(ModelAdmin):
    form = BookAdminForm
    list_display = ("title", "owner", "folder", "page_count", "size", "extracted_text", "uploaded_at")
    list_filter = ("extracted_text", "uploaded_at")
    search_fields = ("title", "owner__username", "owner__email")
    autocomplete_fields = ("owner", "folder")
    readonly_fields = ("page_count", "file_size", "extracted_text", "uploaded_at")
    date_hierarchy = "uploaded_at"
    inlines = (LayerInline,)
    fieldsets = (
        (None, {"fields": ("title", "owner", "folder", "file")}),
        ("Metadata", {"fields": ("page_count", "file_size", "extracted_text", "uploaded_at")}),
    )

    @display(description="Size", ordering="file_size")
    def size(self, book):
        return filesizeformat(book.file_size)
