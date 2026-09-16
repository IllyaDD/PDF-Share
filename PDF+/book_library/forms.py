from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout, Submit
from django import forms
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.template.defaultfilters import filesizeformat

from .models import Book, Folder
from .pdf import inspect_pdf


class FolderForm(forms.ModelForm):
    """Створення/перейменування теки. Вибір батьківської теки обмежений власними."""

    class Meta:
        model = Folder
        fields = ("name", "parent")
        labels = {"name": "Назва теки", "parent": "Батьківська тека"}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        queryset = Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        if self.instance.pk:
            # Тека не може стати власним батьком.
            queryset = queryset.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = queryset
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "— корінь бібліотеки —"

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "name",
            "parent",
            Submit("submit", "Зберегти", css_class="btn btn-primary mt-3"),
        )


class BookForm(forms.ModelForm):
    """
    Завантаження/редагування книги.

    Файл перевіряється по-справжньому: сигнатура, розбір через pymupdf, відсів
    захищених паролем. Кількість сторінок береться там само, під час валідації,
    щоб не відкривати документ двічі.
    """

    class Meta:
        model = Book
        fields = ("title", "folder", "file")
        labels = {"title": "Назва", "folder": "Тека", "file": "PDF-файл"}
        help_texts = {"title": "Залиште порожнім — візьмемо з файлу."}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.page_count = None
        self.pdf_title = ""
        self.has_text_layer = False

        self.fields["folder"].queryset = (
            Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        )
        self.fields["folder"].required = False
        self.fields["folder"].empty_label = "— без теки —"

        self.fields["title"].required = False
        self.fields["file"].widget.attrs["accept"] = ".pdf,application/pdf"
        self.fields["file"].help_text = f"Максимум {filesizeformat(settings.MAX_UPLOAD_SIZE)}"

        if self.instance.pk:
            self.fields["file"].required = False
            self.fields["file"].help_text = "Залиште порожнім, щоб не змінювати файл."

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.form_enctype = "multipart/form-data"
        self.helper.layout = Layout(
            Field("file", template="book_library/fields/pdf_dropzone.html"),
            "title",
            "folder",
            Submit("submit", "Зберегти", css_class="btn btn-primary mt-3"),
        )

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")

        # При редагуванні без заміни файлу сюди приходить наявний FieldFile — не чіпаємо.
        if not isinstance(uploaded, UploadedFile):
            return uploaded

        info = inspect_pdf(uploaded)
        self.page_count = info.page_count
        self.pdf_title = info.title
        self.has_text_layer = info.has_text_layer
        return uploaded

    def clean_title(self):
        # Порожня назва підставляється в clean(), коли вже відомі дані з файлу.
        return (self.cleaned_data.get("title") or "").strip()

    def clean(self):
        cleaned = super().clean()

        if not cleaned.get("title"):
            uploaded = cleaned.get("file")
            fallback = self.pdf_title
            if not fallback and isinstance(uploaded, UploadedFile):
                fallback = uploaded.name.rsplit(".", 1)[0].replace("_", " ").strip()

            if fallback:
                cleaned["title"] = fallback[: Book._meta.get_field("title").max_length]
            else:
                self.add_error("title", "Вкажіть назву — з файлу її дістати не вдалося.")

        return cleaned

    def save(self, commit=True):
        book = super().save(commit=False)

        # page_count заповнений лише коли файл справді завантажували.
        if self.page_count is not None:
            book.page_count = self.page_count
            book.file_size = self.cleaned_data["file"].size
            book.extracted_text = self.has_text_layer

        if commit:
            book.save()
        return book


class BookAdminForm(forms.ModelForm):
    """
    Форма книги для адмінки. Без crispy і без обмеження тек по власнику —
    адмін бачить усе. Файл проходить ту саму перевірку, що й на сайті,
    інакше page_count / file_size / extracted_text розійшлися б із файлом.
    """

    class Meta:
        model = Book
        fields = ("title", "owner", "folder", "file")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pdf_info = None

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")
        if isinstance(uploaded, UploadedFile):
            self._pdf_info = inspect_pdf(uploaded)
        return uploaded

    def save(self, commit=True):
        book = super().save(commit=False)

        if self._pdf_info is not None:
            book.page_count = self._pdf_info.page_count
            book.file_size = self.cleaned_data["file"].size
            book.extracted_text = self._pdf_info.has_text_layer

        if commit:
            book.save()
        return book
