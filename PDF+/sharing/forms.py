from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from django import forms
from django.contrib.auth import get_user_model

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
    """Додавання учасника за username."""

    username = forms.CharField(label="Username учасника", max_length=150)

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "username",
            Submit("add_member", "Додати", css_class="btn btn-outline-primary mt-2"),
        )

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
    """Посилання веде або на книгу, або на теку — рівно на одне з двох."""

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

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "book",
            "folder",
            "expires_at",
            Submit("submit", "Створити посилання", css_class="btn btn-primary mt-3"),
        )

    def clean(self):
        cleaned = super().clean()
        book, folder = cleaned.get("book"), cleaned.get("folder")
        if bool(book) == bool(folder):
            raise forms.ValidationError("Оберіть рівно одне: книгу або теку.")
        return cleaned


class GroupAccessForm(forms.ModelForm):
    """Видача групі доступу до книги або теки. Роздавати можна лише своє."""

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

        # Констрейнт у БД спрацював би і сам, але IntegrityError — не те, що має
        # побачити користувач. Тому дубль ловимо тут і показуємо як помилку форми.
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
