import json
import shutil
import tempfile
from datetime import timedelta

from annotations.models import Annotation, Layer
from annotations.validators import PayloadError, parse_group_name
from book_library.models import Book, Folder
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group as AuthGroup
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from sharing.models import Group, GroupAccess, GroupMembership, ShareLink
from users.models import User

from .dashboard import dashboard_callback
from .testing import FILESYSTEM_STORAGES

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


class AdminRegistrationTests(TestCase):
    def test_admin_site_is_unfold(self):
        from unfold.sites import UnfoldAdminSite

        self.assertIsInstance(admin.site, UnfoldAdminSite)

    def test_business_models_registered(self):
        for model in (User, Folder, Book, Group, ShareLink):
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))

    def test_technical_models_hidden_from_menu(self):
        for model in (Layer, Annotation, GroupMembership, GroupAccess, AuthGroup):
            with self.subTest(model=model.__name__):
                self.assertFalse(admin.site.is_registered(model))

    def test_admin_url_comes_from_settings(self):
        self.assertEqual(reverse("admin:index"), f"/{settings.ADMIN_URL}/")


class AdminPagesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.admin)

    def test_index_renders(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)

    def test_changelists_render(self):
        for name in (
            "users_user",
            "book_library_folder",
            "book_library_book",
            "sharing_group",
            "sharing_sharelink",
        ):
            with self.subTest(changelist=name):
                response = self.client.get(reverse(f"admin:{name}_changelist"))
                self.assertEqual(response.status_code, 200)

    def test_add_forms_render(self):
        for name in ("users_user", "book_library_folder", "book_library_book", "sharing_group"):
            with self.subTest(add=name):
                response = self.client.get(reverse(f"admin:{name}_add"))
                self.assertEqual(response.status_code, 200)

    def test_anonymous_redirected_to_admin_login(self):
        self.client.logout()
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class DashboardTests(TestCase):

    def setUp(self):
        from book_library.tests import make_pdf

        self.teacher = User.objects.create_user(
            username="t", email="t@example.com", password="parol-parol-42", role=User.Role.TEACHER
        )
        self.student = User.objects.create_user(
            username="s", email="s@example.com", password="parol-parol-42", role=User.Role.STUDENT
        )
        self.folder = Folder.objects.create(owner=self.teacher, name="Тека")
        self.book = Book.objects.create(owner=self.teacher, title="Книга", file=make_pdf(), page_count=1)
        layer = Layer.objects.create(book=self.book, owner=self.student)
        Annotation.objects.create(layer=layer, page_number=1, coords=[])
        Group.objects.create(owner=self.teacher, name="Клас")
        ShareLink.objects.create(book=self.book, token="live", created_by=self.teacher)
        ShareLink.objects.create(
            book=self.book,
            token="dead",
            created_by=self.teacher,
            expires_at=timezone.now() - timedelta(days=1),
        )

    def _context(self):
        request = RequestFactory().get("/")
        return dashboard_callback(request, {})

    def test_stats_count_business_objects(self):
        stats = {item["title"]: item["value"] for item in self._context()["stats"]}
        self.assertEqual(stats["Teachers"], 1)
        self.assertEqual(stats["Students"], 1)
        self.assertEqual(stats["Books"], 1)
        self.assertEqual(stats["Folders"], 1)
        self.assertEqual(stats["Annotations"], 1)
        self.assertEqual(stats["Groups"], 1)
        self.assertEqual(stats["Active share links"], 1)

    def test_chart_covers_last_30_days_ending_today(self):
        chart = json.loads(self._context()["activity_chart"])
        self.assertEqual(len(chart["labels"]), 30)
        self.assertEqual(chart["labels"][-1], timezone.localdate().strftime("%d.%m"))
        for dataset in chart["datasets"]:
            self.assertEqual(len(dataset["data"]), 30)

    def test_chart_counts_todays_records_and_ignores_old_ones(self):
        Book.objects.filter(pk=self.book.pk).update(uploaded_at=timezone.now() - timedelta(days=45))

        chart = json.loads(self._context()["activity_chart"])
        by_label = {d["label"]: d["data"] for d in chart["datasets"]}
        self.assertEqual(by_label["Users"][-1], 2)
        self.assertEqual(by_label["Annotations"][-1], 1)
        self.assertEqual(sum(by_label["Books"]), 0)

    def test_index_renders_stats_and_chart(self):
        root = User.objects.create_superuser(
            username="root", email="root@example.com", password="parol-parol-42"
        )
        self.client.force_login(root)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Teachers")
        self.assertContains(response, "Active share links")
        self.assertContains(response, 'data-type="line"')


class AdminLoginRedirectTests(TestCase):

    def setUp(self):
        User.objects.create_superuser(
            username="root", email="root@example.com", password="parol-parol-42"
        )

    def test_login_without_next_lands_on_admin_index(self):
        response = self.client.post(
            reverse("admin:login"), {"username": "root", "password": "parol-parol-42"}
        )
        self.assertRedirects(response, reverse("admin:index"))

    def test_login_keeps_explicit_next(self):
        target = reverse("admin:users_user_changelist")
        response = self.client.post(
            f"{reverse('admin:login')}?next={target}",
            {"username": "root", "password": "parol-parol-42"},
        )
        self.assertRedirects(response, target)


class DashboardLegendTests(TestCase):
    def test_legend_matches_chart_datasets(self):
        request = RequestFactory().get("/")
        context = dashboard_callback(request, {})
        chart = json.loads(context["activity_chart"])

        legend = context["activity_legend"]
        self.assertEqual([item["label"] for item in legend], [d["label"] for d in chart["datasets"]])
        self.assertEqual([item["color"] for item in legend], [d["borderColor"] for d in chart["datasets"]])
        self.assertEqual(len({item["color"] for item in legend}), 3)


class LanguageSwitchTests(TestCase):
    """Перемикач мови: cookie від set_language, LocaleMiddleware і каталог locale/en."""

    def use_english(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    def test_default_language_is_ukrainian(self):
        response = self.client.get(reverse("book_library:home"))
        self.assertContains(response, '<html lang="uk"')
        self.assertContains(response, "Увійти")

    def test_set_language_switches_to_english_and_returns_to_next(self):
        target = reverse("accounts:login")
        response = self.client.post(reverse("set_language"), {"language": "en", "next": target})

        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "en")

        page = self.client.get(target)
        self.assertContains(page, '<html lang="en"')
        self.assertContains(page, "Forgot your password?")
        self.assertNotContains(page, "Забули пароль?")

    def test_set_language_ignores_foreign_next(self):
        response = self.client.post(
            reverse("set_language"), {"language": "en", "next": "https://evil.example/"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.example", response["Location"])

    def test_browser_language_is_used_without_cookie(self):
        response = self.client.get(reverse("book_library:home"), HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        self.assertContains(response, '<html lang="en"')

    def test_unsupported_browser_language_falls_back_to_ukrainian(self):
        response = self.client.get(reverse("book_library:home"), HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(response, '<html lang="uk"')

    def test_flash_message_is_translated(self):
        user = User.objects.create_user(username="reader", email="r@example.com", password="parol-parol-42")
        self.client.force_login(user)
        self.use_english()

        response = self.client.post(reverse("accounts:logout"), follow=True)
        self.assertContains(response, "You have logged out.")

    def test_password_reset_email_follows_language(self):
        User.objects.create_user(username="reader", email="r@example.com", password="parol-parol-42")
        self.use_english()

        self.client.post(reverse("accounts:password_reset"), {"email": "r@example.com"})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Password reset — PDF+")
        self.assertIn("Hello, reader!", mail.outbox[0].body)

    def test_javascript_catalog_serves_reader_strings(self):
        self.use_english()
        response = self.client.get(reverse("javascript-catalog"))
        self.assertContains(response, "All highlights are hidden by the filter.")

    def test_payload_error_is_translated(self):
        with translation.override("en"), self.assertRaisesMessage(
            PayloadError, "name: the topic name cannot be empty"
        ):
            parse_group_name("   ")
