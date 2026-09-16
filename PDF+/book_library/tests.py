import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from users.models import User

from .models import Book, Folder


def build_pdf_bytes(pages=1, title=""):
    """Справжній PDF — форма перевіряє структуру, заглушка з байтів не пройде."""
    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    if title:
        document.set_metadata({"title": title})
    data = document.tobytes()
    document.close()
    return data


PDF_BYTES = build_pdf_bytes()


def make_pdf(name="test.pdf", pages=1, title=""):
    data = build_pdf_bytes(pages, title) if (pages != 1 or title) else PDF_BYTES
    return SimpleUploadedFile(name, data, content_type="application/pdf")


import shutil
import tempfile

from django.test import override_settings

# Тести не мають писати в робочу media/ — FileField кладе файли у MEDIA_ROOT.
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LibraryPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vchytel", email="vchytel@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.folder = Folder.objects.create(owner=self.user, name="Алгебра")
        self.book = Book.objects.create(
            owner=self.user, folder=self.folder, title="Підручник", file=make_pdf()
        )

    def test_library_root_renders(self):
        response = self.client.get(reverse("book_library:library"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Алгебра")

    def test_folder_page_renders_books(self):
        response = self.client.get(reverse("book_library:folder", args=[self.folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Підручник")

    def test_empty_library_shows_empty_state(self):
        Book.objects.all().delete()
        Folder.objects.all().delete()
        response = self.client.get(reverse("book_library:library"))
        self.assertContains(response, "Тут порожньо")

    def test_book_detail_renders(self):
        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Про книгу")

    def test_reader_renders_without_sidebar(self):
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pdf-viewer"')
        self.assertNotContains(response, "app-sidebar")

    def test_shared_with_me_renders(self):
        response = self.client.get(reverse("book_library:shared_with_me"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вам ще нічого не надали")

    def test_forms_render(self):
        for url in [
            reverse("book_library:book_upload"),
            reverse("book_library:folder_create"),
            reverse("book_library:book_edit", args=[self.book.pk]),
            reverse("book_library:folder_edit", args=[self.folder.pk]),
        ]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "form-control")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class OwnershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="vlasnyk", email="vlasnyk@example.com", password="parol-parol-42"
        )
        self.stranger = User.objects.create_user(
            username="chuzhyi", email="chuzhyi@example.com", password="parol-parol-42"
        )
        self.book = Book.objects.create(owner=self.owner, title="Приватна", file=make_pdf())

    def test_stranger_gets_404_on_foreign_book(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        self.assertEqual(response.status_code, 404)

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("book_library:library"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class MutationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="redaktor", email="redaktor@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)

    def test_upload_creates_book(self):
        response = self.client.post(
            reverse("book_library:book_upload"),
            {"title": "Нова книга", "file": make_pdf("nova.pdf")},
        )
        book = Book.objects.get(title="Нова книга")
        self.assertRedirects(response, reverse("book_library:book_detail", args=[book.pk]))
        self.assertEqual(book.owner, self.user)
        self.assertGreater(book.file_size, 0)

    def test_upload_rejects_non_pdf(self):
        response = self.client.post(
            reverse("book_library:book_upload"),
            {"title": "Не PDF", "file": SimpleUploadedFile("photo.png", b"x", "image/png")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Book.objects.filter(title="Не PDF").exists())

    def test_delete_book_requires_post(self):
        book = Book.objects.create(owner=self.user, title="Тимчасова", file=make_pdf())

        self.client.get(reverse("book_library:book_delete", args=[book.pk]))
        self.assertTrue(Book.objects.filter(pk=book.pk).exists())

        self.client.post(reverse("book_library:book_delete", args=[book.pk]))
        self.assertFalse(Book.objects.filter(pk=book.pk).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class UploadValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="zavantazhuvach", email="z@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.url = reverse("book_library:book_upload")

    def _post(self, uploaded, title="Книга"):
        return self.client.post(self.url, {"title": title, "file": uploaded})

    def test_real_pdf_page_count_extracted(self):
        self._post(make_pdf("bagato.pdf", pages=12), title="Багатосторінкова")
        book = Book.objects.get(title="Багатосторінкова")
        self.assertEqual(book.page_count, 12)
        self.assertGreater(book.file_size, 0)

    def test_bytes_with_pdf_extension_rejected(self):
        response = self._post(
            SimpleUploadedFile("brekhun.pdf", b"zovsim ne pdf", "application/pdf")
        )
        self.assertContains(response, "Це не PDF")
        self.assertFalse(Book.objects.exists())

    def test_truncated_pdf_rejected(self):
        # Правильна сигнатура, але тіло обрізане — сигнатури мало.
        broken = build_pdf_bytes(3)[:40]
        response = self._post(SimpleUploadedFile("bytyi.pdf", broken, "application/pdf"))
        self.assertContains(response, "пошкоджений")
        self.assertFalse(Book.objects.exists())

    def test_password_protected_pdf_rejected(self):
        document = pymupdf.open()
        document.new_page()
        data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="taiemnytsia")
        document.close()

        response = self._post(SimpleUploadedFile("zakhyshchenyi.pdf", data, "application/pdf"))
        self.assertContains(response, "захищений паролем")
        self.assertFalse(Book.objects.exists())

    def test_oversized_file_rejected(self):
        with override_settings(MAX_UPLOAD_SIZE=1024):
            response = self._post(make_pdf("velykyi.pdf", pages=20))
        self.assertContains(response, "завеликий")
        self.assertFalse(Book.objects.exists())

    def test_title_falls_back_to_pdf_metadata(self):
        self._post(make_pdf("file.pdf", title="Назва з метаданих"), title="")
        self.assertTrue(Book.objects.filter(title="Назва з метаданих").exists())

    def test_title_falls_back_to_filename(self):
        self._post(make_pdf("moya_knyga.pdf"), title="")
        self.assertTrue(Book.objects.filter(title="moya knyga").exists())

    def test_explicit_title_wins_over_metadata(self):
        self._post(make_pdf("f.pdf", title="З метаданих"), title="Моя назва")
        self.assertTrue(Book.objects.filter(title="Моя назва").exists())

    def test_edit_without_new_file_keeps_metadata(self):
        self._post(make_pdf("orig.pdf", pages=5), title="Оригінал")
        book = Book.objects.get(title="Оригінал")

        self.client.post(
            reverse("book_library:book_edit", args=[book.pk]),
            {"title": "Перейменована", "folder": ""},
        )
        book.refresh_from_db()
        self.assertEqual(book.title, "Перейменована")
        self.assertEqual(book.page_count, 5)

    def test_edit_with_new_file_updates_page_count(self):
        self._post(make_pdf("orig.pdf", pages=5), title="Оригінал")
        book = Book.objects.get(title="Оригінал")

        self.client.post(
            reverse("book_library:book_edit", args=[book.pk]),
            {"title": "Оригінал", "folder": "", "file": make_pdf("novyi.pdf", pages=9)},
        )
        book.refresh_from_db()
        self.assertEqual(book.page_count, 9)

    def test_upload_page_renders_dropzone(self):
        response = self.client.get(self.url)
        self.assertContains(response, "data-dropzone")
        self.assertContains(response, "Перетягніть PDF сюди")
        self.assertContains(response, 'accept=".pdf,application/pdf"')

    def test_saved_file_is_intact_on_disk(self):
        """clean_file() читає потік цілком — без seek(0) на диск ліг би обрізок."""
        self._post(make_pdf("cilyi.pdf", pages=6), title="Цілий")
        book = Book.objects.get(title="Цілий")

        with pymupdf.open(book.file.path) as document:
            self.assertEqual(document.page_count, 6)
        self.assertEqual(book.file.size, book.file_size)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class TextLayerDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="skaner", email="sk@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)

    def _upload(self, data, title):
        self.client.post(
            reverse("book_library:book_upload"),
            {"title": title, "file": SimpleUploadedFile("f.pdf", data, "application/pdf")},
        )
        return Book.objects.get(title=title)

    def test_pdf_with_text_marked_extracted(self):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 100), "Тут є справжній текст", fontsize=12)
        data = document.tobytes()
        document.close()

        book = self._upload(data, "З текстом")
        self.assertTrue(book.extracted_text)

    def test_pdf_without_text_marked_as_scan(self):
        document = pymupdf.open()
        for _ in range(3):
            document.new_page()
        data = document.tobytes()
        document.close()

        book = self._upload(data, "Без тексту")
        self.assertFalse(book.extracted_text)

    def test_reader_warns_about_scan(self):
        document = pymupdf.open()
        document.new_page()
        data = document.tobytes()
        document.close()

        book = self._upload(data, "Скан")
        response = self.client.get(reverse("book_library:book_reader", args=[book.pk]))
        self.assertContains(response, "нема текстового шару")

    def test_reader_silent_for_text_pdf(self):
        document = pymupdf.open()
        document.new_page().insert_text((72, 100), "Текст", fontsize=12)
        data = document.tobytes()
        document.close()

        book = self._upload(data, "Нормальна")
        response = self.client.get(reverse("book_library:book_reader", args=[book.pk]))
        self.assertNotContains(response, "нема текстового шару")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class GroupAccessVisibilityTests(TestCase):
    def setUp(self):
        from sharing.models import Group, GroupAccess, GroupMembership

        self.GroupAccess = GroupAccess
        self.teacher = User.objects.create_user(
            username="vch", email="vch@example.com", password="parol-parol-42",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="uch", email="uch@example.com", password="parol-parol-42"
        )
        self.outsider = User.objects.create_user(
            username="stor", email="stor@example.com", password="parol-parol-42"
        )

        self.group = Group.objects.create(owner=self.teacher, name="9-А")
        GroupMembership.objects.create(group=self.group, user=self.student)

        self.folder = Folder.objects.create(owner=self.teacher, name="Фізика")
        self.subfolder = Folder.objects.create(
            owner=self.teacher, name="Механіка", parent=self.folder
        )
        self.book = Book.objects.create(
            owner=self.teacher, folder=self.folder, title="Підручник фізики", file=make_pdf()
        )
        self.deep_book = Book.objects.create(
            owner=self.teacher, folder=self.subfolder, title="Задачник", file=make_pdf()
        )

    def grant(self, **target):
        return self.GroupAccess.objects.create(
            group=self.group, granted_by=self.teacher, **target
        )

    def test_member_can_open_shared_book(self):
        self.grant(book=self.book)
        self.client.force_login(self.student)

        for url in [
            reverse("book_library:book_detail", args=[self.book.pk]),
            reverse("book_library:book_reader", args=[self.book.pk]),
            reverse("annotations:book_annotations", args=[self.book.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_non_member_still_gets_404(self):
        self.grant(book=self.book)
        self.client.force_login(self.outsider)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 404)

    def test_folder_access_opens_folder_page_and_its_books(self):
        self.grant(folder=self.folder)
        self.client.force_login(self.student)

        response = self.client.get(reverse("book_library:folder", args=[self.folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Підручник фізики")

        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            200,
        )

    def test_folder_access_cascades_to_nested_folders(self):
        self.grant(folder=self.folder)
        self.client.force_login(self.student)

        self.assertEqual(
            self.client.get(
                reverse("book_library:book_reader", args=[self.deep_book.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("book_library:folder", args=[self.subfolder.pk])
            ).status_code,
            200,
        )

    def test_shared_folder_hides_owner_actions(self):
        self.grant(folder=self.folder)
        self.client.force_login(self.student)

        response = self.client.get(reverse("book_library:folder", args=[self.folder.pk]))
        self.assertNotContains(response, "Завантажити PDF</a>")
        self.assertContains(response, "Спільна тека")

    def test_viewer_cannot_edit_or_delete(self):
        self.grant(book=self.book)
        self.client.force_login(self.student)

        self.assertEqual(
            self.client.get(reverse("book_library:book_edit", args=[self.book.pk])).status_code,
            404,
        )
        self.client.post(reverse("book_library:book_delete", args=[self.book.pk]))
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())

    def test_shared_with_me_lists_group_material_without_own(self):
        self.grant(book=self.book)
        own = Book.objects.create(owner=self.student, title="Мій конспект", file=make_pdf())

        self.client.force_login(self.student)
        response = self.client.get(reverse("book_library:shared_with_me"))
        self.assertContains(response, "Підручник фізики")
        self.assertNotContains(response, own.title)

    def test_revoking_access_closes_the_door(self):
        access = self.grant(book=self.book)
        self.client.force_login(self.student)
        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            200,
        )

        access.delete()
        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            404,
        )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ShareLinkVisibilityTests(TestCase):
    def setUp(self):
        from sharing.models import ShareLink

        self.ShareLink = ShareLink
        self.owner = User.objects.create_user(
            username="vlasn", email="vl@example.com", password="parol-parol-42"
        )
        self.book = Book.objects.create(owner=self.owner, title="Відкритий текст", file=make_pdf())

    def test_anonymous_gets_access_after_opening_token(self):
        link = self.ShareLink.objects.create(
            book=self.book, token="dobryi-token", created_by=self.owner
        )

        # Без відкритого посилання — двері зачинені.
        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            404,
        )

        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Відкритий текст")

    def test_expired_token_grants_nothing(self):
        from datetime import timedelta

        from django.utils import timezone

        link = self.ShareLink.objects.create(
            book=self.book,
            token="staryi-token",
            created_by=self.owner,
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            404,
        )

    def test_revoked_link_closes_access_immediately(self):
        link = self.ShareLink.objects.create(
            book=self.book, token="tymchasovyi", created_by=self.owner
        )
        self.client.get(reverse("sharing:share_view", args=[link.token]))
        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            200,
        )

        link.delete()
        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[self.book.pk])).status_code,
            404,
        )

    def test_token_does_not_leak_to_other_books(self):
        other = Book.objects.create(owner=self.owner, title="Приватна", file=make_pdf())
        link = self.ShareLink.objects.create(
            book=self.book, token="tilky-tsia", created_by=self.owner
        )
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        self.assertEqual(
            self.client.get(reverse("book_library:book_reader", args=[other.pk])).status_code,
            404,
        )

    def test_anonymous_reader_has_no_annotation_panel_crash(self):
        link = self.ShareLink.objects.create(
            book=self.book, token="anon-ok", created_by=self.owner
        )
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["annotations"]), [])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ReaderWiringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chytach", email="ch@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user, title="Читанка", file=make_pdf("read.pdf", pages=3)
        )
        self.response = self.client.get(
            reverse("book_library:book_reader", args=[self.book.pk])
        )

    def test_pdfjs_assets_are_wired(self):
        self.assertContains(self.response, "/static/vendor/pdfjs/pdf.min.mjs")
        self.assertContains(self.response, "/static/vendor/pdfjs/pdf.worker.min.mjs")
        self.assertContains(self.response, "/static/vendor/pdfjs/text_layer.css")

    def test_reader_module_loaded_as_esm(self):
        # pdf.js 6 постачається тільки як ESM — без type="module" імпорт не виконається.
        self.assertContains(self.response, 'type="module"')
        self.assertContains(self.response, "/static/js/reader.js")

    def test_viewer_carries_file_url(self):
        self.assertContains(self.response, f'data-file-url="{self.book.file.url}"')

    def test_annotations_payload_present(self):
        self.assertContains(self.response, 'id="annotations-data"')

    def test_static_files_exist_on_disk(self):
        from django.contrib.staticfiles import finders

        for path in [
            "vendor/pdfjs/pdf.min.mjs",
            "vendor/pdfjs/pdf.worker.min.mjs",
            "vendor/pdfjs/text_layer.css",
            "js/reader.js",
        ]:
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), f"{path} не знайдено")

    def test_library_and_worker_versions_match(self):
        """Розбіжність версій бібліотеки і воркера — типова причина мовчазної поломки."""
        import re

        from django.contrib.staticfiles import finders

        def version_of(path):
            text = open(finders.find(path), encoding="utf-8").read()
            return set(re.findall(r'"(\d+\.\d+\.\d+)"', text))

        lib = version_of("vendor/pdfjs/pdf.min.mjs")
        worker = version_of("vendor/pdfjs/pdf.worker.min.mjs")
        self.assertTrue(lib & worker, "спільної версії в бібліотеці й воркері не знайдено")


class InspectPdfTests(TestCase):
    """inspect_pdf — спільна перевірка PDF для форми на сайті та адмінки."""

    def _inspect(self, uploaded):
        from .pdf import inspect_pdf

        return inspect_pdf(uploaded)

    def test_returns_page_count_and_metadata_title(self):
        info = self._inspect(make_pdf(pages=7, title="Фізика"))
        self.assertEqual(info.page_count, 7)
        self.assertEqual(info.title, "Фізика")
        self.assertFalse(info.has_text_layer)

    def test_detects_text_layer(self):
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Текст є")
        data = document.tobytes()
        document.close()

        info = self._inspect(SimpleUploadedFile("t.pdf", data, "application/pdf"))
        self.assertTrue(info.has_text_layer)

    def test_rewinds_stream_after_inspection(self):
        uploaded = make_pdf(pages=2)
        self._inspect(uploaded)
        self.assertEqual(uploaded.tell(), 0)

    def test_rejects_non_pdf_bytes(self):
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "Це не PDF"):
            self._inspect(SimpleUploadedFile("x.pdf", b"ne pdf", "application/pdf"))

    def test_rejects_truncated_pdf(self):
        from django.core.exceptions import ValidationError

        broken = build_pdf_bytes(3)[:40]
        with self.assertRaisesMessage(ValidationError, "пошкоджений"):
            self._inspect(SimpleUploadedFile("x.pdf", broken, "application/pdf"))

    def test_rejects_password_protected_pdf(self):
        from django.core.exceptions import ValidationError

        document = pymupdf.open()
        document.new_page()
        data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw")
        document.close()

        with self.assertRaisesMessage(ValidationError, "захищений паролем"):
            self._inspect(SimpleUploadedFile("x.pdf", data, "application/pdf"))

    @override_settings(MAX_UPLOAD_SIZE=10)
    def test_rejects_oversized_file(self):
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "завеликий"):
            self._inspect(make_pdf())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BookAdminFormTests(TestCase):
    """Адмін-форма книги рахує метадані так само, як форма на сайті."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="admin-owner", email="ao@example.com", password="parol-parol-42"
        )

    def _form(self, data, files=None, instance=None):
        from .forms import BookAdminForm

        return BookAdminForm(data=data, files=files, instance=instance)

    def test_save_fills_metadata_from_file(self):
        form = self._form(
            {"title": "Адмінська", "owner": self.user.pk},
            {"file": make_pdf(pages=9)},
        )
        self.assertTrue(form.is_valid(), form.errors)
        book = form.save()
        self.assertEqual(book.page_count, 9)
        self.assertEqual(book.file_size, book.file.size)
        self.assertFalse(book.extracted_text)

    def test_invalid_file_is_form_error(self):
        form = self._form(
            {"title": "Брехня", "owner": self.user.pk},
            {"file": SimpleUploadedFile("x.pdf", b"ne pdf", "application/pdf")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Це не PDF", form.errors["file"][0])

    def test_edit_without_new_file_keeps_metadata(self):
        book = Book.objects.create(
            owner=self.user, title="Стара", file=make_pdf(pages=4), page_count=4, file_size=123
        )
        form = self._form({"title": "Нова назва", "owner": self.user.pk}, instance=book)
        self.assertTrue(form.is_valid(), form.errors)
        book = form.save()
        self.assertEqual(book.title, "Нова назва")
        self.assertEqual(book.page_count, 4)
        self.assertEqual(book.file_size, 123)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BookAdminViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.admin)
        self.book = Book.objects.create(
            owner=self.admin, title="Була", file=make_pdf(pages=2), page_count=2
        )

    def _post(self, data):
        payload = {
            "title": self.book.title,
            "owner": self.admin.pk,
            "layers-TOTAL_FORMS": "0",
            "layers-INITIAL_FORMS": "0",
            "layers-MIN_NUM_FORMS": "0",
            "layers-MAX_NUM_FORMS": "1000",
        }
        payload.update(data)
        return self.client.post(
            reverse("admin:book_library_book_change", args=[self.book.pk]), payload
        )

    def test_replacing_file_in_admin_recounts_pages(self):
        response = self._post({"file": make_pdf("nova.pdf", pages=11)})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context.get("errors"))
        self.book.refresh_from_db()
        self.assertEqual(self.book.page_count, 11)
        self.assertGreater(self.book.file_size, 0)

    def test_broken_file_in_admin_rejected(self):
        response = self._post({"file": SimpleUploadedFile("z.pdf", b"ne pdf", "application/pdf")})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Це не PDF")
        self.book.refresh_from_db()
        self.assertEqual(self.book.page_count, 2)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BookMoveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sortuvalnyk", email="s@example.com", password="parol-parol-42"
        )
        self.stranger = User.objects.create_user(
            username="storonnii", email="st@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.folder = Folder.objects.create(owner=self.user, name="Фізика")
        self.foreign_folder = Folder.objects.create(owner=self.stranger, name="Чужа")
        self.book = Book.objects.create(owner=self.user, title="Механіка", file=make_pdf())
        self.url = reverse("book_library:book_move", args=[self.book.pk])

    def test_move_into_folder(self):
        response = self.client.post(self.url, {"folder": self.folder.pk})
        self.book.refresh_from_db()
        self.assertEqual(self.book.folder, self.folder)
        self.assertRedirects(response, reverse("book_library:folder", args=[self.folder.pk]))

    def test_remove_from_folder(self):
        self.book.folder = self.folder
        self.book.save()
        response = self.client.post(self.url, {"folder": ""})
        self.book.refresh_from_db()
        self.assertIsNone(self.book.folder)
        self.assertRedirects(response, reverse("book_library:library"))

    def test_next_param_used_when_safe(self):
        next_url = reverse("book_library:book_detail", args=[self.book.pk])
        response = self.client.post(self.url, {"folder": self.folder.pk, "next": next_url})
        self.assertRedirects(response, next_url)

    def test_next_param_ignored_when_external(self):
        response = self.client.post(
            self.url, {"folder": self.folder.pk, "next": "https://evil.example/"}
        )
        self.assertRedirects(response, reverse("book_library:folder", args=[self.folder.pk]))

    def test_foreign_folder_rejected(self):
        response = self.client.post(self.url, {"folder": self.foreign_folder.pk})
        self.assertEqual(response.status_code, 404)
        self.book.refresh_from_db()
        self.assertIsNone(self.book.folder)

    def test_stranger_cannot_move(self):
        self.client.force_login(self.stranger)
        response = self.client.post(self.url, {"folder": self.foreign_folder.pk})
        self.assertEqual(response.status_code, 404)
        self.book.refresh_from_db()
        self.assertIsNone(self.book.folder)

    def test_get_not_allowed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class LibrarySearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="shukach", email="sh@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.folder = Folder.objects.create(owner=self.user, name="Математика")
        self.subfolder = Folder.objects.create(owner=self.user, parent=self.folder, name="Геометрія")
        self.other = Folder.objects.create(owner=self.user, name="Історія")

        self.in_folder = Book.objects.create(
            owner=self.user, folder=self.folder, title="Алгебра 7 клас", file=make_pdf()
        )
        self.in_subfolder = Book.objects.create(
            owner=self.user, folder=self.subfolder, title="Планіметрія", file=make_pdf()
        )
        self.elsewhere = Book.objects.create(
            owner=self.user, folder=self.other, title="Алгебра історії", file=make_pdf()
        )
        self.at_root = Book.objects.create(owner=self.user, title="Алгебра логіки", file=make_pdf())

    def test_folder_search_includes_subfolders_only(self):
        url = reverse("book_library:folder", args=[self.folder.pk])
        response = self.client.get(url, {"q": "алгебра"})
        self.assertContains(response, "Алгебра 7 клас")
        self.assertNotContains(response, "Алгебра історії")
        self.assertNotContains(response, "Алгебра логіки")

        response = self.client.get(url, {"q": "планіметрія"})
        self.assertContains(response, "Планіметрія")

    def test_root_search_covers_whole_library(self):
        response = self.client.get(reverse("book_library:library"), {"q": "алгебра"})
        self.assertContains(response, "Алгебра 7 клас")
        self.assertContains(response, "Алгебра історії")
        self.assertContains(response, "Алгебра логіки")
        self.assertNotContains(response, "Планіметрія")

    def test_search_hides_folder_cards(self):
        response = self.client.get(reverse("book_library:library"), {"q": "алгебра"})
        self.assertNotContains(response, 'href="/library/folder/%d/"' % self.other.pk)

    def test_empty_result_message(self):
        response = self.client.get(reverse("book_library:library"), {"q": "хімія"})
        self.assertContains(response, "Нічого не знайдено")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class FolderAddBooksTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="napovniuvach", email="n@example.com", password="parol-parol-42"
        )
        self.stranger = User.objects.create_user(
            username="inshyi", email="i@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.folder = Folder.objects.create(owner=self.user, name="Хімія")
        self.inside = Book.objects.create(
            owner=self.user, folder=self.folder, title="Органіка", file=make_pdf()
        )
        self.outside = Book.objects.create(owner=self.user, title="Неорганіка", file=make_pdf())
        self.foreign = Book.objects.create(owner=self.stranger, title="Чужа біологія", file=make_pdf())
        self.url = reverse("book_library:folder_add_books", args=[self.folder.pk])

    def test_lists_only_own_books_outside_folder(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неорганіка")
        self.assertNotContains(response, "Органіка")
        self.assertNotContains(response, "Чужа біологія")

    def test_search_filters_candidates(self):
        Book.objects.create(owner=self.user, title="Фізика", file=make_pdf())
        response = self.client.get(self.url, {"q": "неорг"})
        self.assertContains(response, "Неорганіка")
        self.assertNotContains(response, "Фізика")

    def test_stranger_gets_404(self):
        self.client.force_login(self.stranger)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
