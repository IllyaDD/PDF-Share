from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Field, Layout, Submit
from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.template.defaultfilters import filesizeformat

from .models import Book, Folder
from .pdf import inspect_pdf


def apply_pdf_info(book, info, uploaded):
    book.page_count = info.page_count
    book.file_size = uploaded.size
    book.extracted_text = info.has_text_layer
    if info.cover:
        book.cover.save("cover.jpg", ContentFile(info.cover), save=False)
    else:
        book.cover = ""


UPLOAD_PROGRESS_HTML = """
<div class="upload-progress mt-3 d-none" data-upload-progress>
  <div class="progress" style="height: .5rem;" role="progressbar"
       aria-label="Поступ завантаження" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
    <div class="progress-bar" style="width: 0%" data-upload-bar></div>
  </div>
  <p class="small text-body-secondary mt-2 mb-0" data-upload-status>Завантаження…</p>
</div>
"""


class FolderForm(forms.ModelForm):

    class Meta:
        model = Folder
        fields = ("name", "parent", "is_catalog")
        labels = {
            "name": "Назва теки",
            "parent": "Батьківська тека",
            "is_catalog": "Показувати в каталозі",
        }
        help_texts = {"is_catalog": "Теку разом з усім вкладеним побачить кожен користувач."}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)

        can_manage_catalog = owner is not None and owner.is_staff
        if not can_manage_catalog:
            del self.fields["is_catalog"]

        queryset = Folder.objects.none() if owner is None else Folder.objects.filter(owner=owner)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = queryset
        self.fields["parent"].required = False
        self.fields["parent"].empty_label = "— корінь бібліотеки —"

        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "name",
            "parent",
            *(["is_catalog"] if can_manage_catalog else []),
            Submit("submit", "Зберегти", css_class="btn btn-primary mt-3"),
        )


class BookForm(forms.ModelForm):

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
        self._pdf_info = None

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
            HTML(UPLOAD_PROGRESS_HTML),
        )

    def clean_file(self):
        uploaded = self.cleaned_data.get("file")

        if not isinstance(uploaded, UploadedFile):
            return uploaded

        info = inspect_pdf(uploaded)
        self._pdf_info = info
        self.page_count = info.page_count
        self.pdf_title = info.title
        self.has_text_layer = info.has_text_layer
        return uploaded

    def clean_title(self):
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

        if self._pdf_info is not None:
            apply_pdf_info(book, self._pdf_info, self.cleaned_data["file"])

        if commit:
            book.save()
        return book


class BookAdminForm(forms.ModelForm):

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
            apply_pdf_info(book, self._pdf_info, self.cleaned_data["file"])

        if commit:
            book.save()
        return book
