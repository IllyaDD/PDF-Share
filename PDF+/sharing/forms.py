from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from book_library.models import Book, Folder

from .models import Group, GroupAccess, GroupMembership, ShareLink

User = get_user_model()


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name",)
        labels = {"name": "Назва групи"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "name",
            Submit("submit", "Зберегти", css_class="btn btn-primary mt-3"),
        )


class MembershipForm(forms.Form):

    username = forms.CharField(label="Username учасника", max_length=150)

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        add_button = Submit("add_member", "Додати")
        add_button.field_classes = "btn btn-outline-primary mt-2"

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout("username", add_button)

    def clean_username(self):
        username = self.cleaned_data["username"]
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise forms.ValidationError("Користувача з таким username не знайдено.")

        if self.group and GroupMembership.objects.filter(group=self.group, user=user).exists():
            raise forms.ValidationError("Цей користувач уже в групі.")

        self.cleaned_user = user
        return username


class ShareLinkForm(forms.ModelForm):

    class Meta:
        model = ShareLink
        fields = ("book", "folder", "expires_at")
        labels = {"book": "Книга", "folder": "Тека", "expires_at": "Діє до"}
        widgets = {"expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}
        help_texts = {"expires_at": "Порожньо — посилання безстрокове."}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["book"].queryset = (
            Book.objects.none() if owner is None else Book.objects.filter(owner=owner)
        )
        self.fields["folder"].queryset = (
            Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        )
        self.fields["book"].empty_label = "— не обрано —"
        self.fields["folder"].empty_label = "— не обрано —"
        self.fields["expires_at"].widget.attrs["min"] = timezone.now().strftime("%Y-%m-%dT%H:%M")

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "book",
            "folder",
            "expires_at",
            Submit("submit", "Створити посилання", css_class="btn btn-primary mt-3"),
        )

    def clean_expires_at(self):
        expires_at = self.cleaned_data.get("expires_at")
        if expires_at is not None and expires_at <= timezone.now():
            raise forms.ValidationError("Дата завершення не може бути в минулому.")
        return expires_at

    def clean(self):
        cleaned = super().clean()
        book, folder = cleaned.get("book"), cleaned.get("folder")
        if bool(book) == bool(folder):
            raise forms.ValidationError("Оберіть рівно одне: книгу або теку.")
        return cleaned


class GroupAccessForm(forms.ModelForm):

    class Meta:
        model = GroupAccess
        fields = ("group", "book", "folder", "permission")
        labels = {
            "group": "Група",
            "book": "Книга",
            "folder": "Тека",
            "permission": "Права",
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
        self.fields["book"].empty_label = "— не обрано —"
        self.fields["folder"].empty_label = "— не обрано —"

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "group",
            "book",
            "folder",
            "permission",
            Submit("submit", "Надати доступ", css_class="btn btn-primary mt-3"),
        )

    def clean(self):
        cleaned = super().clean()
        group, book, folder = cleaned.get("group"), cleaned.get("book"), cleaned.get("folder")

        if bool(book) == bool(folder):
            raise forms.ValidationError("Оберіть рівно одне: книгу або теку.")

        if group:
            duplicate = GroupAccess.objects.filter(group=group, book=book, folder=folder)
            if self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                target = book or folder
                raise forms.ValidationError(
                    f"Група «{group.name}» уже має доступ до «{target}»."
                )

        return cleaned
