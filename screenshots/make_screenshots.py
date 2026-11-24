"""Скриншоти інтерфейсу PDF+ для README і презентацій.

Працює з dev-базою з ``PDF+/.env`` (не з тимчасовою):

1. Сіє демо-дані ідемпотентно — повторний запуск нічого не дублює. Чіпає лише своє:
   «Організатор Демо» (``organizer.demo``) з книгами, теками, групою «10-А» і демо-текою
   каталогу; «Учасник Демо» (``member.demo``) — член групи. Пароль обох — ``demo-pass-2026``.
   Наявні користувачі, книги й теки dev-бази не змінюються. Для адмінки береться перший
   наявний суперюзер.
2. Книги — PDF з аргументів. Без аргументів беруться книги, які ви самі завантажили на сайті,
   увійшовши як ``organizer.demo`` (теки «Бібліотека», без папки). Виділення ставляться на
   справжній текстовий шар головної книги (PyMuPDF), тож PDF має бути не сканом.
3. Піднімає live-server Django у потоці (окремий runserver не потрібен) і знімає сторінки
   через Playwright (Chromium). Sentry у процесі скрипта вимкнений.

Запуск з кореня репозиторію (playwright ставиться лише на час запуску, pyproject.toml не
змінюється):

    uv run --with playwright python screenshots/make_screenshots.py main.pdf extra1.pdf extra2.pdf
    uv run --with playwright python screenshots/make_screenshots.py --only 06 07   # книги вже в базі
    uv run --with playwright python screenshots/make_screenshots.py --reset        # прибрати демо

Найкраще — 3+ PDF: головна книга й частина решти лягають у корінь бібліотеки (картки на 02),
інші — у вкладену теку «Тема 1 › Урок 1» (03), копії всіх — у демо-теку каталогу (04).

Якщо Chromium для цієї версії playwright ще не завантажений:

    uv run --with playwright playwright install chromium
"""

import argparse
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "PDF+"
DEFAULT_OUT = Path(__file__).resolve().parent

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

PASSWORD = "demo-pass-2026"
ORGANIZER = {"username": "organizer.demo", "email": "organizer.demo@example.com",
             "first_name": "Організатор", "last_name": "Демо"}
MEMBER = {"username": "member.demo", "email": "member.demo@example.com",
          "first_name": "Учасник", "last_name": "Демо"}
GROUP_NAME = "10-А"
TOPIC_NAME = "Тема 1"
NESTED_NAME = "Урок 1"
CATALOG_NAME = "Підручники 10 класу"
LINK_TOKEN = "demo-share-link"
LINK_EXPIRES = "2026-12-31T18:00"

# Пʼять кольорів Annotation.PALETTE — на 06 мають бути всі.
ORGANIZER_NOTES = [
    ("#FFEB3B", "Ключове визначення — винести в конспект.", "Визначення"),
    ("#A5D6A7", "Буде на контрольній роботі.", "До контрольної"),
    ("#90CAF9", "Порівняйте з прикладом на наступній сторінці.", None),
    ("#F48FB1", "Дата, яку треба запамʼятати.", "До контрольної"),
    ("#FFCC80", "Обговоримо на уроці.", None),
]
MEMBER_NOTES = [
    ("#F48FB1", "Не зовсім зрозумів — запитати на уроці."),
    ("#FFCC80", "Повторити перед тестом."),
]

SHOTS = {
    "01": "01_login.png",
    "02": "02_library.png",
    "03": "03_folder.png",
    "04": "04_catalog.png",
    "05": "05_upload.png",
    "06": "06_viewer.png",
    "07": "07_viewer_selection.png",
    "08": "08_viewer_organizer_tab.png",
    "09": "09_new_link_form.png",
    "10": "10_links_page.png",
    "11": "11_group_page.png",
    "12": "12_guest_card.png",
    "13": "13_guest_reader.png",
    "14": "14_admin_books.png",
    "15": "15_mobile_library.png",
    "16": "16_mobile_viewer.png",
    # 17_mobile_notes.png не знімається, поки панель нотаток на телефоні зверстана колонкою
    # праворуч замість нижньої панелі.
}
SKIPPED = {"17": "17_mobile_notes.png — пропущено: панель нотаток на телефоні ще не виправлена."}


def parse_args():
    parser = argparse.ArgumentParser(description="Скриншоти PDF+ на dev-базі з демо-даними.")
    parser.add_argument("pdfs", nargs="*", type=Path,
                        help="PDF-книги; перша — головна. Без них — уже завантажені книги organizer.demo.")
    parser.add_argument("--title", help="Назва головної книги (за замовчуванням — з метаданих PDF або ім'я файлу).")
    parser.add_argument("--page", type=int, help="Сторінка з виділеннями (за замовчуванням — найщільніша з перших 15).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Куди класти PNG (за замовчуванням — тека скрипта).")
    parser.add_argument("--scale", type=float, default=2, help="deviceScaleFactor (2 — чіткі скриншоти для README).")
    parser.add_argument("--only", nargs="*", metavar="NN", help="Лише ці кадри, напр. --only 06 07 14.")
    parser.add_argument("--headed", action="store_true", help="Показати вікно браузера.")
    parser.add_argument("--reset", action="store_true",
                        help="Видалити демо-користувачів з усіма їхніми книгами, теками й групами та вийти.")
    args = parser.parse_args()
    for pdf in args.pdfs:
        if not pdf.is_file():
            parser.error(f"файл не знайдено: {pdf}")
    return args


# --- Django ------------------------------------------------------------------------------

def setup_django():
    # Змінні оточення мають пріоритет над .env у python-decouple.
    os.environ["SENTRY_DSN"] = ""      # жодних подій у справжній проєкт Sentry
    os.environ["DEBUG"] = "true"       # media через django.conf.urls.static
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "book_sharing_settings.settings")
    os.chdir(PROJECT)
    sys.path.insert(0, str(PROJECT))

    import django
    django.setup()

    from django.conf import settings
    db = settings.DATABASES["default"]
    print(f"База: {db['NAME']} @ {db.get('HOST') or 'localhost'}")
    if settings.USE_R2:
        print("Увага: USE_R2=true — демо-PDF підуть у бакет R2 з .env.")


def start_live_server():
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.test.testcases import LiveServerThread

    thread = LiveServerThread("localhost", StaticFilesHandler, port=0)
    thread.daemon = True
    thread.start()
    thread.is_ready.wait()
    if thread.error:
        raise thread.error
    return thread


# --- Демо-дані ---------------------------------------------------------------------------

def reset():
    from django.contrib.auth import get_user_model

    from book_library.folders import delete_folder
    from book_library.models import Book, Folder

    # Демо-тека каталогу належить суперюзеру, тож каскадом від демо-користувачів не зникне.
    for folder in Folder.objects.filter(name=CATALOG_NAME, is_catalog=True, parent=None):
        Book.objects.filter(folder=folder).delete()
        delete_folder(folder)
    from annotations.models import Annotation

    users = get_user_model().objects.filter(username__in=[ORGANIZER["username"], MEMBER["username"]])
    # Спершу нотатки окремо: post_delete пише тумбстоун api.DeletedAnnotation на власника шару,
    # і під час каскаду від користувача цей рядок посилався б на вже видаленого (IntegrityError).
    Annotation.objects.filter(layer__owner__in=users).delete()
    deleted = users.delete()
    # Книги, теки, групи, шари й посилання каскадяться; файли прибирає book_library/signals.py.
    print(f"Видалено: {deleted[1] or 'нічого'}")


def demo_user(spec):
    from django.contrib.auth import get_user_model

    user, created = get_user_model().objects.get_or_create(
        username=spec["username"],
        defaults={**spec, "email_verified": True},
    )
    if created:
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])
    return user


def pick_lines(data, page_number=None):
    """Рядки тексту сторінки з 5+ словами: координати в PDF user space і частки сторінки."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        candidates = [page_number - 1] if page_number else range(min(document.page_count, 15))
        best = None
        for index in candidates:
            page = document[index]
            lines = {}
            for x0, y0, x1, y1, word, block, line, _n in page.get_text("words"):
                lines.setdefault((block, line), []).append((x0, y0, x1, y1, word))
            usable = [words for words in lines.values() if len(words) >= 5]
            if best is None or len(usable) > len(best[1]):
                best = (index, usable, page)
        if not best or len(best[1]) < 8:
            raise SystemExit(
                "У головній книзі замало тексту для виділень: потрібна сторінка щонайменше з 8 "
                "рядками по 5+ слів. Передайте --page або інший PDF (не скан)."
            )
        index, lines, page = best
        to_pdf = ~page.transformation_matrix  # MuPDF (згори вниз) → PDF user space (знизу вгору)
        result = []
        for words in sorted(lines, key=lambda w: (w[0][1], w[0][0])):
            words = words[:9]
            rect = pymupdf.Rect(words[0][0], min(w[1] for w in words),
                                words[-1][2], max(w[3] for w in words))
            pdf_rect = rect * to_pdf
            result.append({
                "text": " ".join(w[4] for w in words),
                "coords": {"x0": pdf_rect.x0, "y0": pdf_rect.y0, "x1": pdf_rect.x1, "y1": pdf_rect.y1},
                # Частки від розміру сторінки — для drag-виділення в браузері.
                "frac": (rect.x0 / page.rect.width, rect.x1 / page.rect.width,
                         (rect.y0 + rect.y1) / 2 / page.rect.height),
            })
        return index + 1, result


def ensure_book(owner, folder, name, data, title=None):
    """Книга власника в теці з такою назвою; якщо нема — заливає PDF через inspect_pdf()."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from book_library.forms import apply_pdf_info
    from book_library.models import Book
    from book_library.pdf import inspect_pdf

    uploaded = SimpleUploadedFile(name, data, content_type="application/pdf")
    info = inspect_pdf(uploaded)
    title = title or info.title or Path(name).stem.replace("_", " ")
    book = Book.objects.filter(owner=owner, folder=folder, title=title).first()
    if book is None:
        book = Book(owner=owner, folder=folder, title=title)
        book.file = uploaded
        apply_pdf_info(book, info, uploaded)
        book.save()
    return book


def sources(args, organizer):
    """[(ім'я файлу, байти, назва або None)] — з аргументів або з книг, залитих на сайті."""
    from book_library.models import Book

    if args.pdfs:
        return [(pdf.name, pdf.read_bytes(), args.title if i == 0 else None)
                for i, pdf in enumerate(args.pdfs)]

    books = list(Book.objects.filter(owner=organizer, folder=None).order_by("uploaded_at"))
    if args.title:
        books.sort(key=lambda book: book.title != args.title)
    if not books:
        raise SystemExit(
            f"Нема жодної книги. Передайте PDF аргументом або увійдіть на сайті як "
            f"{ORGANIZER['username']} / {PASSWORD} і завантажте книгу, потім запустіть скрипт знову."
        )
    result = []
    for book in books:
        with book.file.open("rb") as handle:
            result.append((Path(book.file.name).name, handle.read(), book.title))
    return result


def seed(args):
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from annotations.models import Annotation, Layer, LayerPublication, NoteGroup
    from book_library.models import Favorite, Folder, ReadingProgress
    from sharing.models import Group, GroupAccess, ShareLink
    from sharing.views import _join_group

    organizer = demo_user(ORGANIZER)
    member = demo_user(MEMBER)
    admin = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by("pk").first()

    topic, _created = Folder.objects.get_or_create(owner=organizer, name=TOPIC_NAME, parent=None)
    nested, _created = Folder.objects.get_or_create(owner=organizer, name=NESTED_NAME, parent=topic)
    Folder.objects.get_or_create(owner=organizer, name="Домашні завдання", parent=None)
    if admin is None:
        raise SystemExit("У dev-базі нема активного суперюзера: він потрібен для теки каталогу й адмінки.")
    # Каталог веде адміністратор — інакше тека лізе в сайдбар і лічильник книг організатора.
    catalog, _created = Folder.objects.get_or_create(
        owner=admin, name=CATALOG_NAME, parent=None, defaults={"is_catalog": True},
    )

    items = sources(args, organizer)
    main = ensure_book(organizer, None, *items[0])
    # Корінь — головна й кожна друга, вкладена тека — решта (з однією книгою — її копія).
    for i, item in enumerate(items[1:], start=1):
        ensure_book(organizer, nested if i % 2 else None, *item)
    if len(items) == 1:
        ensure_book(organizer, nested, *items[0])
    for item in items:
        ensure_book(admin, catalog, *item)

    group, _created = Group.objects.get_or_create(owner=organizer, name=GROUP_NAME)
    _join_group(group, member)
    GroupAccess.objects.get_or_create(group=group, book=main, defaults={"granted_by": organizer})
    GroupAccess.objects.get_or_create(group=group, folder=topic, defaults={"granted_by": organizer})

    with main.file.open("rb") as handle:
        page_number, lines = pick_lines(handle.read(), args.page)
    pick = lambda fraction: lines[min(int(len(lines) * fraction), len(lines) - 1)]  # noqa: E731

    # Нотатки демо-шарів перестворюються щоразу: сторінка могла змінитися (--page, інша книга).
    layer, _created = Layer.objects.get_or_create(book=main, owner=organizer)
    layer.annotations.all().delete()
    topics = {name: NoteGroup.objects.get_or_create(layer=layer, name=name)[0]
              for name in {t for _c, _n, t in ORGANIZER_NOTES if t}}
    for fraction, (color, note, topic_name) in zip([0.06, 0.22, 0.38, 0.56, 0.72], ORGANIZER_NOTES):
        line = pick(fraction)
        Annotation.objects.create(layer=layer, page_number=page_number, coords=[line["coords"]],
                                  selected_text=line["text"], note_text=note, color=color,
                                  group=topics.get(topic_name))
    LayerPublication.objects.get_or_create(layer=layer, group=group)

    own, _created = Layer.objects.get_or_create(book=main, owner=member)
    own.annotations.all().delete()
    for fraction, (color, note) in zip([0.82, 0.92], MEMBER_NOTES):
        line = pick(fraction)
        Annotation.objects.create(layer=own, page_number=page_number, coords=[line["coords"]],
                                  selected_text=line["text"], note_text=note, color=color)

    now = timezone.now()
    ReadingProgress.objects.update_or_create(
        user=organizer, book=main, defaults={"last_page": page_number, "read_at": now},
    )
    Favorite.objects.get_or_create(user=organizer, book=main)

    link, _created = ShareLink.objects.get_or_create(
        token=LINK_TOKEN, defaults={"book": main, "created_by": organizer, "include_annotations": True},
    )
    if link.book_id != main.pk:
        link.book = main
        link.save(update_fields=["book"])

    return {
        "organizer": organizer, "member": member, "admin": admin,
        "main": main, "nested": nested, "catalog": catalog, "group": group, "layer": layer,
        "link": link, "page": page_number, "selection": pick(0.47),
    }


def session_cookie(user, base_url):
    from django.conf import settings
    from django.test import Client

    client = Client()
    client.force_login(user)
    return {"name": settings.SESSION_COOKIE_NAME,
            "value": client.cookies[settings.SESSION_COOKIE_NAME].value, "url": base_url}


# --- Playwright ---------------------------------------------------------------------------

class Shooter:
    def __init__(self, browser, base_url, out, scale, wanted):
        self.browser = browser
        self.base = base_url
        self.out = out
        self.scale = scale
        self.wanted = wanted
        self.failed = []

    def want(self, number):
        return not self.wanted or number in self.wanted

    def context(self, cookie=None, mobile=False):
        context = self.browser.new_context(
            viewport=MOBILE if mobile else DESKTOP,
            device_scale_factor=self.scale,
            is_mobile=mobile, has_touch=mobile,
            locale="uk-UA", color_scheme="light",
        )
        if cookie is not None:
            context.add_cookies([cookie])
        return context

    def save(self, page, number, full_page=False):
        page.screenshot(path=str(self.out / SHOTS[number]), full_page=full_page, animations="disabled")
        print(f"  ✓ {SHOTS[number]}")

    def run(self, number, action):
        if not self.want(number):
            return
        try:
            action()
        except Exception:
            self.failed.append(SHOTS[number])
            print(f"  ✗ {SHOTS[number]}")
            traceback.print_exc()

    def open(self, context, path):
        page = context.new_page()
        page.goto(self.base + path, wait_until="networkidle")
        return page


def wait_reader(page, page_number, marks=True):
    selector = f'.pdf-page[data-page-number="{page_number}"]'
    page.wait_for_selector(f"{selector} .textLayer span", timeout=30_000)
    if marks:
        page.wait_for_selector(f"{selector} .annotation-rect", timeout=10_000)
    page.wait_for_timeout(600)  # догрузка шрифтів і плавна прокрутка до сторінки
    return page.locator(selector)


def select_line(page, page_div, frac):
    page_div.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    box = page_div.bounding_box()
    x0, x1, y = (box["x"] + frac[0] * box["width"] + 2, box["x"] + frac[1] * box["width"] - 2,
                 box["y"] + frac[2] * box["height"])
    page.mouse.move(x0, y)
    page.mouse.down()
    page.mouse.move(x1, y, steps=12)
    page.mouse.up()
    page.wait_for_selector("#highlightPalette:not([hidden])", timeout=5_000)


def shoot(args, data, base_url):
    from django.conf import settings
    from playwright.sync_api import sync_playwright

    args.out.mkdir(parents=True, exist_ok=True)
    reader = f"/books/{data['main'].pk}/read/?page={data['page']}"
    # Сесії — до старту Playwright: його sync API крутить event loop, і ORM у ньому падає
    # з SynchronousOnlyOperation.
    roles = ["organizer", "member"] + (["admin"] if data["admin"] else [])
    cookies = {role: session_cookie(data[role], base_url) for role in roles}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        s = Shooter(browser, base_url, args.out, args.scale, set(args.only or []))

        guest = s.context()
        organizer = s.context(cookies["organizer"])
        member = s.context(cookies["member"])
        mobile = s.context(cookies["organizer"], mobile=True)

        def viewer():
            page = s.open(organizer, reader)
            wait_reader(page, data["page"])
            s.save(page, "06")

        def selection():
            page = s.open(organizer, reader)
            page_div = wait_reader(page, data["page"])
            select_line(page, page_div, data["selection"]["frac"])
            s.save(page, "07")

        def organizer_tab():
            page = s.open(member, f"{reader}&layer={data['layer'].pk}")
            wait_reader(page, data["page"])
            page.wait_for_selector("[data-layer-readonly]:not(.d-none)")
            s.save(page, "08")

        def new_link_form():
            page = s.open(organizer, f"/links/new/?book={data['main'].pk}&annotations=1")
            field = page.locator("input[name=expires_at]")
            field.fill(LINK_EXPIRES)
            field.blur()
            # Форма вища за екран; вищий вʼюпорт замість full_page, щоб сайдбар тягнувся до низу.
            page.set_viewport_size({"width": DESKTOP["width"], "height": 1150})
            s.save(page, "09")

        def guest_reader():
            page = s.open(guest, f"/s/{LINK_TOKEN}/")
            page.get_by_role("link", name="Відкрити книгу").click()
            page.wait_for_url("**/read/**")
            # Посилання веде на с. 1 з ?layer=; переходимо на сторінку з нотатками автора.
            page.goto(f"{page.url}&page={data['page']}", wait_until="networkidle")
            wait_reader(page, data["page"])
            s.save(page, "13")

        def admin_books():
            if "admin" not in cookies:
                raise RuntimeError("у dev-базі нема активного суперюзера")
            page = s.open(s.context(cookies["admin"]),
                          f"/{settings.ADMIN_URL}/book_library/book/?folder__id__exact={data['catalog'].pk}")
            s.save(page, "14")

        def mobile_viewer():
            page = s.open(mobile, reader)
            wait_reader(page, data["page"])
            s.save(page, "16")

        s.run("01", lambda: s.save(s.open(guest, "/accounts/login/"), "01"))
        s.run("02", lambda: s.save(s.open(organizer, "/library/"), "02"))
        s.run("03", lambda: s.save(s.open(organizer, f"/library/folder/{data['nested'].pk}/"), "03"))
        s.run("04", lambda: s.save(s.open(organizer, f"/library/folder/{data['catalog'].pk}/"), "04"))
        s.run("05", lambda: s.save(s.open(organizer, "/books/upload/"), "05"))
        s.run("06", viewer)
        s.run("07", selection)
        s.run("08", organizer_tab)
        s.run("09", new_link_form)
        s.run("10", lambda: s.save(s.open(organizer, "/links/"), "10"))
        s.run("11", lambda: s.save(s.open(organizer, f"/groups/{data['group'].pk}/"), "11", full_page=True))
        s.run("12", lambda: s.save(s.open(guest, f"/s/{LINK_TOKEN}/"), "12"))
        s.run("13", guest_reader)
        s.run("14", admin_books)
        s.run("15", lambda: s.save(s.open(mobile, "/library/"), "15"))
        s.run("16", mobile_viewer)

        browser.close()
    return s.failed


def main():
    args = parse_args()
    args.pdfs = [pdf.resolve() for pdf in args.pdfs]
    args.out = args.out.resolve()
    setup_django()

    if args.reset:
        reset()
        return

    print("Сію демо-дані…")
    data = seed(args)
    server = start_live_server()
    try:
        base_url = f"http://localhost:{server.port}"
        print(f"Live-server: {base_url}\nЗнімаю в {args.out}")
        failed = shoot(args, data, base_url)
    finally:
        server.terminate()

    for note in SKIPPED.values():
        print(f"  – {note}")
    print(f"Демо-вхід: {ORGANIZER['username']} / {MEMBER['username']}. "
          f"Прибрати: --reset")
    if failed:
        print(f"Не вдалося: {', '.join(failed)}")
        sys.exit(1)
    print("Готово.")


if __name__ == "__main__":
    main()
