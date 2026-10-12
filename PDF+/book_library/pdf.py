import logging
from dataclasses import dataclass

import pymupdf
from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"

COVER_WIDTH = 480
COVER_JPEG_QUALITY = 80

TEXT_PROBE_PAGES = 8


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    title: str
    has_text_layer: bool
    cover: bytes = b""


def render_cover(document):
    page = document[0]
    zoom = COVER_WIDTH / page.rect.width
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pixmap.tobytes("jpg", jpg_quality=COVER_JPEG_QUALITY)


def _has_text_layer(document):
    total = document.page_count
    step = max(1, total // TEXT_PROBE_PAGES)
    for index in range(0, total, step):
        if document[index].get_text().strip():
            return True
    return False


def inspect_pdf(uploaded):
    if uploaded.size > settings.MAX_UPLOAD_SIZE:
        raise ValidationError(
            _("Файл завеликий: %(size)s. Максимум — %(max)s.") % {
                "size": filesizeformat(uploaded.size),
                "max": filesizeformat(settings.MAX_UPLOAD_SIZE),
            }
        )

    data = uploaded.read()
    uploaded.seek(0)

    if not data.startswith(PDF_MAGIC):
        raise ValidationError(
            _("Це не PDF. Перевірте файл — саме розширення .pdf нічого не гарантує.")
        )

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ValidationError(_("Файл пошкоджений — не вдалося його розібрати."))

    with document:
        if document.needs_pass:
            raise ValidationError(_("PDF захищений паролем. Зніміть пароль і завантажте ще раз."))
        if document.page_count == 0:
            raise ValidationError(_("У документі нема жодної сторінки."))

        try:
            cover = render_cover(document)
        except Exception:
            logger.exception("Не вдалося відрендерити обкладинку для %s", uploaded.name)
            cover = b""

        return PdfInfo(
            page_count=document.page_count,
            title=((document.metadata or {}).get("title") or "").strip(),
            has_text_layer=_has_text_layer(document),
            cover=cover,
        )
