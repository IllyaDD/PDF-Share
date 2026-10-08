import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from users.models import User

from .models import Book, Folder


def build_pdf_bytes(pages=1, title=""):
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

from book_sharing_settings.testing import FILESYSTEM_STORAGES
from django.test import override_settings

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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

    def test_stored_key_is_uuid_not_user_filename(self):
        self._post(make_pdf("Moya Taemna Knyga.pdf"), title="Таємна")
        book = Book.objects.get(title="Таємна")

        self.assertRegex(book.file.name, r"^books/[0-9a-f]{32}\.pdf$")
        self.assertNotIn("Taemna", book.file.name)

    def test_same_filename_twice_gets_distinct_keys(self):
        self._post(make_pdf("same.pdf"), title="Перша")
        self._post(make_pdf("same.pdf"), title="Друга")
        first = Book.objects.get(title="Перша")
        second = Book.objects.get(title="Друга")

        self.assertNotEqual(first.file.name, second.file.name)

    def test_upload_page_renders_dropzone(self):
        response = self.client.get(self.url)
        self.assertContains(response, "data-dropzone")
        self.assertContains(response, "Перетягніть PDF сюди")
        self.assertContains(response, 'accept=".pdf,application/pdf"')

    def test_saved_file_is_intact_on_disk(self):
        self._post(make_pdf("cilyi.pdf", pages=6), title="Цілий")
        book = Book.objects.get(title="Цілий")

        with pymupdf.open(book.file.path) as document:
            self.assertEqual(document.page_count, 6)
        self.assertEqual(book.file.size, book.file_size)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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
        import re

        from django.contrib.staticfiles import finders

        def version_of(path):
            text = open(finders.find(path), encoding="utf-8").read()
            return set(re.findall(r'"(\d+\.\d+\.\d+)"', text))

        lib = version_of("vendor/pdfjs/pdf.min.mjs")
        worker = version_of("vendor/pdfjs/pdf.worker.min.mjs")
        self.assertTrue(lib & worker, "спільної версії в бібліотеці й воркері не знайдено")


class InspectPdfTests(TestCase):

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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class BookAdminFormTests(TestCase):

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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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

    def _folder_link(self, folder):
        return 'href="%s"' % reverse("book_library:folder", args=[folder.pk])

    def test_default_scope_finds_folders_and_books(self):
        Book.objects.create(owner=self.user, title="Історія України", file=make_pdf())
        response = self.client.get(reverse("book_library:library"), {"q": "історі"})
        self.assertContains(response, self._folder_link(self.other))
        self.assertContains(response, "Історія України")
        self.assertContains(response, "Алгебра історії")
        self.assertEqual(response.context["result_count"], 3)

    def test_folders_scope_skips_books(self):
        response = self.client.get(
            reverse("book_library:library"), {"q": "історі", "scope": "folders"}
        )
        self.assertContains(response, self._folder_link(self.other))
        self.assertNotContains(response, "Алгебра історії")

    def test_books_scope_skips_folders(self):
        response = self.client.get(
            reverse("book_library:library"), {"q": "історі", "scope": "books"}
        )
        self.assertNotContains(response, self._folder_link(self.other))
        self.assertContains(response, "Алгебра історії")

    def test_folder_search_covers_nested_folders_but_not_itself(self):
        url = reverse("book_library:folder", args=[self.folder.pk])
        response = self.client.get(url, {"q": "метрія", "scope": "folders"})
        self.assertEqual(response.context["folders"], [self.subfolder])

        response = self.client.get(url, {"q": "математика", "scope": "folders"})
        self.assertEqual(response.context["folders"], [])

    def test_folder_search_ignores_foreign_folders(self):
        stranger = User.objects.create_user(
            username="chuzhyi", email="ch@example.com", password="parol-parol-42"
        )
        Folder.objects.create(owner=stranger, name="Історія чужа")
        response = self.client.get(reverse("book_library:library"), {"q": "історія чужа"})
        self.assertEqual(response.context["folders"], [])

    def test_unknown_scope_falls_back_to_all(self):
        response = self.client.get(
            reverse("book_library:library"), {"q": "історі", "scope": "garbage"}
        )
        self.assertEqual(response.context["search_scope"], "all")
        self.assertContains(response, self._folder_link(self.other))

    def test_empty_result_message(self):
        response = self.client.get(reverse("book_library:library"), {"q": "хімія"})
        self.assertContains(response, "Нічого не знайдено")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class CatalogTests(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin", email="admin@example.com", password="parol-parol-42",
            is_staff=True,
        )
        self.reader = User.objects.create_user(
            username="chytach", email="ch@example.com", password="parol-parol-42"
        )

        self.catalog = Folder.objects.create(owner=self.admin, name="Шкільна програма", is_catalog=True)
        self.nested = Folder.objects.create(owner=self.admin, name="10 клас", parent=self.catalog)
        self.book = Book.objects.create(
            owner=self.admin, folder=self.catalog, title="Біологія", file=make_pdf()
        )
        self.nested_book = Book.objects.create(
            owner=self.admin, folder=self.nested, title="Хімія", file=make_pdf()
        )
        self.private = Folder.objects.create(owner=self.admin, name="Чернетки")
        self.private_book = Book.objects.create(
            owner=self.admin, folder=self.private, title="Приватна", file=make_pdf()
        )

    def test_catalog_page_lists_catalog_folders_only(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Шкільна програма")
        self.assertNotContains(response, "10 клас")
        self.assertNotContains(response, "Чернетки")

    def test_catalog_page_requires_login(self):
        response = self.client.get(reverse("book_library:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_reader_opens_catalog_folder_and_nested_book(self):
        self.client.force_login(self.reader)

        response = self.client.get(reverse("book_library:folder", args=[self.catalog.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Біологія")
        self.assertContains(response, "10 клас")

        for url in [
            reverse("book_library:folder", args=[self.nested.pk]),
            reverse("book_library:book_detail", args=[self.nested_book.pk]),
            reverse("book_library:book_reader", args=[self.nested_book.pk]),
            reverse("annotations:book_annotations", args=[self.nested_book.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_private_admin_material_stays_hidden(self):
        self.client.force_login(self.reader)
        self.assertEqual(
            self.client.get(reverse("book_library:folder", args=[self.private.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("book_library:book_reader", args=[self.private_book.pk])
            ).status_code,
            404,
        )

    def test_anonymous_cannot_read_catalog_book(self):
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 404)

    def test_catalog_folder_shows_catalog_badge_and_crumbs(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:folder", args=[self.nested.pk]))
        self.assertContains(response, "Каталог")
        self.assertNotContains(response, "Спільна тека")
        self.assertNotContains(response, "Завантажити PDF</a>")
        self.assertContains(response, f'href="{reverse("book_library:catalog")}"')

    def test_reader_cannot_modify_catalog(self):
        self.client.force_login(self.reader)

        self.assertEqual(
            self.client.get(reverse("book_library:folder_edit", args=[self.catalog.pk])).status_code,
            404,
        )
        self.client.post(reverse("book_library:folder_delete", args=[self.catalog.pk]))
        self.assertTrue(Folder.objects.filter(pk=self.catalog.pk).exists())

        own = Book.objects.create(owner=self.reader, title="Моя", file=make_pdf())
        self.client.post(
            reverse("book_library:book_move", args=[own.pk]), {"folder": self.catalog.pk}
        )
        own.refresh_from_db()
        self.assertIsNone(own.folder)

    def test_catalog_not_listed_as_shared_with_me(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:shared_with_me"))
        self.assertNotContains(response, "Шкільна програма")
        self.assertNotContains(response, "Біологія")

    def test_catalog_search_covers_whole_tree(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:catalog"), {"q": "хім"})
        self.assertContains(response, "Хімія")
        self.assertNotContains(response, "Біологія")
        self.assertNotContains(response, "Приватна")

    def test_only_staff_sees_catalog_checkbox_in_folder_form(self):
        self.client.force_login(self.reader)
        self.assertNotContains(
            self.client.get(reverse("book_library:folder_create")), 'name="is_catalog"'
        )

        self.client.force_login(self.admin)
        self.assertContains(
            self.client.get(reverse("book_library:folder_create")), 'name="is_catalog"'
        )

    def test_non_staff_cannot_flag_folder_as_catalog(self):
        self.client.force_login(self.reader)
        self.client.post(
            reverse("book_library:folder_create"), {"name": "Хитра", "is_catalog": "on"}
        )
        folder = Folder.objects.get(name="Хитра")
        self.assertFalse(folder.is_catalog)

    def test_staff_creates_catalog_folder_from_site(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("book_library:folder_create"), {"name": "Нова секція", "is_catalog": "on"}
        )
        self.assertTrue(Folder.objects.get(name="Нова секція").is_catalog)

    def test_sidebar_links_to_catalog(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:library"))
        self.assertContains(response, reverse("book_library:catalog"))

    def test_empty_catalog_shows_empty_state(self):
        Folder.objects.filter(is_catalog=True).update(is_catalog=False)
        self.client.force_login(self.reader)
        response = self.client.get(reverse("book_library:catalog"))
        self.assertContains(response, "Каталог порожній")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class StorageCleanupTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="prybyralnyk", email="prybyralnyk@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(owner=self.user, title="Книга", file=make_pdf())
        self.storage = self.book.file.storage
        self.assertTrue(self.storage.exists(self.book.file.name))

    def test_delete_removes_file_from_storage(self):
        name = self.book.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("book_library:book_delete", args=[self.book.pk]))
        self.assertFalse(Book.objects.filter(pk=self.book.pk).exists())
        self.assertFalse(self.storage.exists(name))

    def test_queryset_delete_removes_files(self):
        other = Book.objects.create(owner=self.user, title="Ще одна", file=make_pdf())
        names = [self.book.file.name, other.file.name]
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(owner=self.user).delete()
        for name in names:
            self.assertFalse(self.storage.exists(name))

    def test_cascade_from_user_removes_file(self):
        name = self.book.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()
        self.assertFalse(self.storage.exists(name))

    def test_replacing_file_removes_old_one(self):
        old_name = self.book.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("book_library:book_edit", args=[self.book.pk]),
                {"title": "Книга", "file": make_pdf("novyi.pdf", pages=2)},
            )
        self.book.refresh_from_db()
        self.assertNotEqual(self.book.file.name, old_name)
        self.assertFalse(self.storage.exists(old_name))
        self.assertTrue(self.storage.exists(self.book.file.name))

    def test_editing_title_keeps_file(self):
        name = self.book.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("book_library:book_edit", args=[self.book.pk]),
                {"title": "Перейменована"},
            )
        self.book.refresh_from_db()
        self.assertEqual(self.book.title, "Перейменована")
        self.assertEqual(self.book.file.name, name)
        self.assertTrue(self.storage.exists(name))

    def test_file_survives_rolled_back_delete(self):
        from django.db import transaction

        pk, name = self.book.pk, self.book.file.name
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.book.delete()
                    raise RuntimeError("відкат")
            except RuntimeError:
                pass
        self.assertTrue(Book.objects.filter(pk=pk).exists())
        self.assertTrue(self.storage.exists(name))

    def test_storage_error_does_not_break_delete(self):
        from unittest.mock import patch

        from django.core.files.storage import FileSystemStorage

        pk = self.book.pk
        with self.assertLogs("book_library.signals", level="ERROR"), patch.object(
            FileSystemStorage, "delete", side_effect=OSError("R2 down")
        ), self.captureOnCommitCallbacks(execute=True):
            self.book.delete()
        self.assertFalse(Book.objects.filter(pk=pk).exists())


JPEG_MAGIC = b"\xff\xd8\xff"


class CoverRenderTests(TestCase):

    def test_inspect_pdf_renders_cover_jpeg(self):
        from .pdf import inspect_pdf

        info = inspect_pdf(make_pdf(pages=3))
        self.assertTrue(info.cover.startswith(JPEG_MAGIC))

    def test_cover_width_is_bounded(self):
        from .pdf import COVER_WIDTH, inspect_pdf

        info = inspect_pdf(make_pdf())
        pixmap = pymupdf.Pixmap(info.cover)
        self.assertEqual(pixmap.width, COVER_WIDTH)

    def test_render_failure_leaves_cover_empty(self):
        from unittest.mock import patch

        from .pdf import inspect_pdf

        with (
            patch("book_library.pdf.render_cover", side_effect=RuntimeError("boom")),
            self.assertLogs("book_library.pdf", "ERROR"),
        ):
            info = inspect_pdf(make_pdf())
        self.assertEqual(info.cover, b"")
        self.assertEqual(info.page_count, 1)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class CoverStorageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="obkladynka",
            email="obkladynka@example.com",
            password="parol-parol-42",
        )
        self.client.force_login(self.user)

    def _upload(self, **extra):
        self.client.post(
            reverse("book_library:book_upload"),
            {"title": "З обкладинкою", "file": make_pdf(**extra)},
        )
        return Book.objects.get(title="З обкладинкою")

    def test_upload_saves_cover_to_storage(self):
        book = self._upload()
        self.assertTrue(book.cover.name.startswith("covers/"))
        self.assertTrue(book.cover.name.endswith(".jpg"))
        self.assertTrue(book.cover.storage.exists(book.cover.name))
        with book.cover.open("rb") as handle:
            self.assertTrue(handle.read(3).startswith(JPEG_MAGIC))

    def test_admin_form_saves_cover(self):
        from .forms import BookAdminForm

        form = BookAdminForm(
            data={"title": "Адмінська", "owner": self.user.pk},
            files={"file": make_pdf()},
        )
        self.assertTrue(form.is_valid(), form.errors)
        book = form.save()
        self.assertTrue(book.cover.name)
        self.assertTrue(book.cover.storage.exists(book.cover.name))

    def test_delete_removes_cover_from_storage(self):
        book = self._upload()
        cover_name = book.cover.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("book_library:book_delete", args=[book.pk]))
        self.assertFalse(book.cover.storage.exists(cover_name))

    def test_replacing_file_rerenders_and_removes_old_cover(self):
        book = self._upload()
        old_cover = book.cover.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("book_library:book_edit", args=[book.pk]),
                {"title": "З обкладинкою", "file": make_pdf("novyi.pdf", pages=2)},
            )
        book.refresh_from_db()
        self.assertNotEqual(book.cover.name, old_cover)
        self.assertFalse(book.cover.storage.exists(old_cover))
        self.assertTrue(book.cover.storage.exists(book.cover.name))

    def test_editing_title_keeps_cover(self):
        book = self._upload()
        cover_name = book.cover.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("book_library:book_edit", args=[book.pk]),
                {"title": "Перейменована"},
            )
        book.refresh_from_db()
        self.assertEqual(book.cover.name, cover_name)
        self.assertTrue(book.cover.storage.exists(cover_name))

    def test_book_card_shows_cover_image(self):
        book = self._upload()
        response = self.client.get(reverse("book_library:library"))
        self.assertContains(response, f'src="{book.cover.url}"')

    def test_book_card_without_cover_shows_icon(self):
        Book.objects.create(owner=self.user, title="Стара книга", file=make_pdf())
        response = self.client.get(reverse("book_library:library"))
        self.assertContains(response, "bi-file-earmark-pdf")
        self.assertNotContains(response, "book-cover-img")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class RenderCoversCommandTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="bekfil", email="bekfil@example.com", password="parol-parol-42"
        )

    def test_command_renders_missing_covers_only(self):
        from io import StringIO

        from django.core.management import call_command

        without = Book.objects.create(owner=self.user, title="Без", file=make_pdf())
        self.assertFalse(without.cover.name)

        out = StringIO()
        call_command("render_covers", stdout=out)

        without.refresh_from_db()
        self.assertTrue(without.cover.name)
        self.assertTrue(without.cover.storage.exists(without.cover.name))

        first_name = without.cover.name
        call_command("render_covers", stdout=out)
        without.refresh_from_db()
        self.assertEqual(without.cover.name, first_name)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class ReadingProgressTests(TestCase):
    def setUp(self):
        from sharing.models import ShareLink

        self.ShareLink = ShareLink
        self.user = User.objects.create_user(
            username="chytach", email="ch@example.com", password="parol-parol-42"
        )
        self.stranger = User.objects.create_user(
            username="chuzhyi", email="chuzh@example.com", password="parol-parol-42"
        )
        self.book = Book.objects.create(
            owner=self.user, title="Роман", file=make_pdf(), page_count=10
        )
        self.foreign_book = Book.objects.create(
            owner=self.stranger, title="Чужа", file=make_pdf(), page_count=10
        )
        self.client.force_login(self.user)

    def post_page(self, book, body):
        import json

        return self.client.post(
            reverse("book_library:book_progress", args=[book.pk]),
            data=body if isinstance(body, str) else json.dumps(body),
            content_type="application/json",
        )

    def test_post_creates_and_updates_progress(self):
        from .models import ReadingProgress

        self.assertEqual(self.post_page(self.book, {"page": 3}).status_code, 204)
        self.assertEqual(ReadingProgress.objects.get(user=self.user, book=self.book).last_page, 3)

        self.assertEqual(self.post_page(self.book, {"page": 7}).status_code, 204)
        progress = ReadingProgress.objects.get(user=self.user, book=self.book)
        self.assertEqual(progress.last_page, 7)
        self.assertEqual(progress.percent, 70)

    def test_invalid_payload_is_400(self):
        for body in [{"page": 0}, {"page": 11}, {"page": "5"}, {"page": True}, {}, "не json", "[1]"]:
            with self.subTest(body=body):
                self.assertEqual(self.post_page(self.book, body).status_code, 400)

    def test_get_not_allowed(self):
        response = self.client.get(reverse("book_library:book_progress", args=[self.book.pk]))
        self.assertEqual(response.status_code, 405)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.post_page(self.book, {"page": 2})
        self.assertEqual(response.status_code, 302)

    def test_invisible_book_is_404(self):
        self.assertEqual(self.post_page(self.foreign_book, {"page": 2}).status_code, 404)

    def test_share_link_in_session_allows_progress(self):
        link = self.ShareLink.objects.create(
            book=self.foreign_book, token="progress-token", created_by=self.stranger
        )
        self.client.get(reverse("sharing:share_view", args=[link.token]))
        self.assertEqual(self.post_page(self.foreign_book, {"page": 4}).status_code, 204)

    def test_record_progress_last_write_wins_by_read_at(self):
        from datetime import timedelta

        from django.utils import timezone

        from .progress import record_progress

        now = timezone.now()
        record_progress(self.user, self.book, 8, now - timedelta(minutes=1))
        stale = record_progress(self.user, self.book, 2, now - timedelta(hours=1))
        self.assertEqual(stale.last_page, 8)

        fresh = record_progress(self.user, self.book, 9, now)
        self.assertEqual(fresh.last_page, 9)

    def test_record_progress_clamps_future_read_at(self):
        from datetime import timedelta

        from django.utils import timezone

        from .progress import record_progress

        progress = record_progress(
            self.user, self.book, 5, timezone.now() + timedelta(days=365)
        )
        self.assertLessEqual(progress.read_at, timezone.now())

    def test_home_lists_visible_progress_newest_first(self):
        from datetime import timedelta

        from django.utils import timezone

        from .progress import record_progress

        second = Book.objects.create(owner=self.user, title="Повість", file=make_pdf(), page_count=4)
        now = timezone.now()
        record_progress(self.user, self.book, 5, now - timedelta(hours=2))
        record_progress(self.user, second, 1, now - timedelta(hours=1))
        record_progress(self.user, self.foreign_book, 3, now)  # доступу нема — не показувати

        response = self.client.get(reverse("book_library:home"))
        self.assertContains(response, "Продовжити читати")
        titles = [p.book.title for p in response.context["continue_reading"]]
        self.assertEqual(titles, ["Повість", "Роман"])

    def test_home_without_progress_or_anonymous_has_no_block(self):
        self.assertNotContains(self.client.get(reverse("book_library:home")), "Продовжити читати")
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("book_library:home")), "Продовжити читати")

    def test_detail_and_reader_show_saved_page(self):
        from .progress import record_progress

        detail_url = reverse("book_library:book_detail", args=[self.book.pk])
        reader_url = reverse("book_library:book_reader", args=[self.book.pk])
        self.assertNotContains(self.client.get(detail_url), "Продовжити з с.")
        self.assertContains(self.client.get(reader_url), 'data-last-page=""')

        record_progress(self.user, self.book, 6)
        self.assertContains(self.client.get(detail_url), "Продовжити з с. 6")
        reader = self.client.get(reader_url)
        self.assertContains(reader, 'data-last-page="6"')
        self.assertContains(
            reader, reverse("book_library:book_progress", args=[self.book.pk])
        )

    def test_anonymous_reader_has_no_progress_wiring(self):
        link = self.ShareLink.objects.create(
            book=self.book, token="anon-progress", created_by=self.user
        )
        self.client.logout()
        self.client.get(reverse("sharing:share_view", args=[link.token]))
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-progress-url")
