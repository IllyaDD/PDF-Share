"""
Перевірка завантаженого PDF. Одне місце для форми на сайті та адмінки, щоб
page_count / file_size / extracted_text рахувались однаково, звідки б файл не прийшов.
"""

from dataclasses import dataclass

import pymupdf
from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

PDF_MAGIC = b"%PDF-"

# Скільки сторінок пробувати на наявність тексту. Скани трапляються з текстовою
# обкладинкою, тому проби розкидані по документу, а не беруться підряд з початку.
TEXT_PROBE_PAGES = 8


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    title: str
    has_text_layer: bool


def _has_text_layer(document):
    """Чи є в документі вибирається текст. Від цього залежить, чи працює виділення."""
    total = document.page_count
    step = max(1, total // TEXT_PROBE_PAGES)
    for index in range(0, total, step):
        if document[index].get_text().strip():
            return True
    return False


def inspect_pdf(uploaded):
    """
    Перевіряє файл по-справжньому: розмір, сигнатура, розбір через pymupdf,
    відсів захищених паролем і порожніх. Повертає PdfInfo, кидає ValidationError.
    Позицію у потоці повертає на початок, щоб файл потім зберігся цілим.
    """
    if uploaded.size > settings.MAX_UPLOAD_SIZE:
        raise ValidationError(
            f"Файл завеликий: {filesizeformat(uploaded.size)}. "
            f"Максимум — {filesizeformat(settings.MAX_UPLOAD_SIZE)}."
        )

    data = uploaded.read()
    uploaded.seek(0)

    if not data.startswith(PDF_MAGIC):
        raise ValidationError(
            "Це не PDF. Перевірте файл — саме розширення .pdf нічого не гарантує."
        )

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ValidationError("Файл пошкоджений — не вдалося його розібрати.")

    with document:
        if document.needs_pass:
            raise ValidationError("PDF захищений паролем. Зніміть пароль і завантажте ще раз.")
        if document.page_count == 0:
            raise ValidationError("У документі нема жодної сторінки.")

        return PdfInfo(
            page_count=document.page_count,
            title=((document.metadata or {}).get("title") or "").strip(),
            has_text_layer=_has_text_layer(document),
        )
