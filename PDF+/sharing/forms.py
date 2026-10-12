from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from annotations.models import NoteGroup
from book_library.models import Book, Folder

from .models import Group, GroupAccess, GroupMembership, ShareLink

User = get_user_model()


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name",)
        labels = {"name": _("Назва групи")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "name",
            Submit("submit", _("Зберегти"), css_class="btn btn-primary mt-3"),
        )


class MembershipForm(forms.Form):

    username = forms.CharField(label=_("Username учасника"), max_length=150)

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        add_button = Submit("add_member", _("Додати"))
        add_button.field_classes = "btn btn-outline-primary mt-2"

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout("username", add_button)

    def clean_username(self):
        username = self.cleaned_data["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise forms.ValidationError(_("Користувача з таким username не знайдено."))

        if self.group and GroupMembership.objects.filter(group=self.group, user=user).exists():
            raise forms.ValidationError(_("Цей користувач уже в групі."))

        self.cleaned_user = user
        return username


class NoteGroupCheckboxes(forms.CheckboxSelectMultiple):
    """Чекбокси тем із data-book: скрипт форми лишає видимими теми обраної книги."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-book"] = instance.layer.book_id
        return option


class ShareLinkForm(forms.ModelForm):

    class Meta:
        model = ShareLink
        fields = ("book", "folder", "include_annotations", "note_groups", "expires_at")
        labels = {
            "book": _("Книга"),
            "folder": _("Тека"),
            "include_annotations": _("Що побачить отримувач"),
            "note_groups": _("Які теми показати"),
            "expires_at": _("Діє до"),
        }
        widgets = {
            "include_annotations": forms.RadioSelect(
                choices=[(False, _("Лише книгу")), (True, _("Книгу з моїми нотатками"))]
            ),
            "note_groups": NoteGroupCheckboxes,
            "expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        help_texts = {
            "include_annotations": _("Нотатки відкриються окремим шаром, лише для читання."),
            "note_groups": _("Нічого не обрано — усі нотатки книги. Лише для посилання на книгу."),
            "expires_at": _("Порожньо — посилання безстрокове."),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["book"].queryset = (
            Book.objects.none() if owner is None else Book.objects.filter(owner=owner)
        )
        self.fields["folder"].queryset = (
            Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        )
        self.fields["book"].empty_label = _("— не обрано —")
        self.fields["folder"].empty_label = _("— не обрано —")
        self.fields["expires_at"].widget.attrs["min"] = timezone.now().strftime("%Y-%m-%dT%H:%M")

        groups = (
            NoteGroup.objects.none()
            if owner is None
            else NoteGroup.objects.filter(layer__owner=owner)
            .select_related("layer__book")
            .order_by("layer__book__title", "name")
        )
        if groups.exists():
            self.fields["note_groups"].queryset = groups
            self.fields["note_groups"].label_from_instance = (
                lambda group: f"{group.layer.book.title} — {group.name}"
            )
        else:
            del self.fields["note_groups"]

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "book",
            "folder",
            "include_annotations",
            *(["note_groups"] if "note_groups" in self.fields else []),
            "expires_at",
            Submit("submit", _("Створити посилання"), css_class="btn btn-primary mt-3"),
        )

    def clean_expires_at(self):
        expires_at = self.cleaned_data.get("expires_at")
        if expires_at is not None and expires_at <= timezone.now():
            raise forms.ValidationError(_("Дата завершення не може бути в минулому."))
        return expires_at

    def clean(self):
        cleaned = super().clean()
        book, folder = cleaned.get("book"), cleaned.get("folder")
        if bool(book) == bool(folder):
            raise forms.ValidationError(_("Оберіть рівно одне: книгу або теку."))

        groups = cleaned.get("note_groups")
        if groups:
            if not book or not cleaned.get("include_annotations"):
                raise forms.ValidationError(
                    _("Теми можна обрати лише для посилання на книгу з нотатками.")
                )
            if any(group.layer.book_id != book.pk for group in groups):
                self.add_error("note_groups", _("Обрані теми мають належати обраній книзі."))
        return cleaned

    def save(self, commit=True):
        link = super().save(commit=False)
        link.annotation_scope = (
            ShareLink.AnnotationScope.SELECTED
            if self.cleaned_data.get("note_groups")
            else ShareLink.AnnotationScope.ALL
        )
        if commit:
            link.save()
            self.save_m2m()
        return link


class GroupAccessForm(forms.ModelForm):

    class Meta:
        model = GroupAccess
        fields = ("group", "book", "folder", "permission")
        labels = {
            "group": _("Група"),
            "book": _("Книга"),
            "folder": _("Тека"),
            "permission": _("Права"),
        }

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["group"].queryset = (
            Group.objects.none() if owner is None else Group.objects.filter(owner=owner)
        )
        self.fields["book"].queryset = (
            Book.objects.none() if owner is None else Book.objects.filter(owner=owner)
        )
        self.fields["folder"].queryset = (
            Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        )
        self.fields["book"].required = False
        self.fields["folder"].required = False
        self.fields["book"].empty_label = _("— не обрано —")
        self.fields["folder"].empty_label = _("— не обрано —")

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "group",
            "book",
            "folder",
            "permission",
            Submit("submit", _("Надати доступ"), css_class="btn btn-primary mt-3"),
        )

    def clean(self):
        cleaned = super().clean()
        group, book, folder = cleaned.get("group"), cleaned.get("book"), cleaned.get("folder")

        if bool(book) == bool(folder):
            raise forms.ValidationError(_("Оберіть рівно одне: книгу або теку."))

        if group:
            duplicate = GroupAccess.objects.filter(group=group, book=book, folder=folder)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                target = book or folder
                raise forms.ValidationError(
                    _("Група «%(group)s» уже має доступ до «%(target)s».") % {"group": group.name, "target": target}
                )

        return cleaned
