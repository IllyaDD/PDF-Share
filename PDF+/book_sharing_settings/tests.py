"""Тести адмінки: реєстрація моделей, шлях з .env, тема Unfold, dashboard."""

import json
from datetime import timedelta

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group as AuthGroup
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from annotations.models import Annotation, Layer
from book_library.models import Book, Folder
from sharing.models import Group, GroupAccess, GroupMembership, ShareLink
from users.models import User

from .dashboard import dashboard_callback


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


class DashboardTests(TestCase):
    """Статистика і графік активності на головній адмінки."""

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
    """Логін через адмінку веде в адмінку, а не на LOGIN_REDIRECT_URL сайту."""

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
