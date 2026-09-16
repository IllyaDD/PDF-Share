from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from book_library.models import Book, Folder
from users.models import User

from .models import Group, GroupAccess, GroupMembership, ShareLink


def make_book(owner, title="Книга"):
    return Book.objects.create(
        owner=owner,
        title=title,
        file=SimpleUploadedFile("b.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    )


import shutil
import tempfile

from django.test import override_settings

# Тести не мають писати в робочу media/ — FileField кладе файли у MEDIA_ROOT.
TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class GroupPageTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="vchytelka", email="v@example.com", password="parol-parol-42",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="uchenytsia", email="u@example.com", password="parol-parol-42"
        )
        self.group = Group.objects.create(owner=self.teacher, name="10-А")
        GroupMembership.objects.create(group=self.group, user=self.student)

    def test_teacher_sees_create_button(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("sharing:group_list"))
        self.assertContains(response, "Нова група")
        self.assertContains(response, "10-А")

    def test_student_sees_group_without_create_button(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("sharing:group_list"))
        self.assertContains(response, "10-А")
        self.assertNotContains(response, "Нова група")

    def test_group_detail_lists_members(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uchenytsia")
        self.assertContains(response, "Додати учасника")

    def test_member_sees_detail_without_member_form(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Додати учасника")

    def test_outsider_redirected(self):
        outsider = User.objects.create_user(
            username="storonniy", email="s@example.com", password="parol-parol-42"
        )
        self.client.force_login(outsider)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertRedirects(response, reverse("sharing:group_list"))

    def test_add_member_by_username(self):
        newcomer = User.objects.create_user(
            username="novachok", email="n@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.teacher)
        self.client.post(
            reverse("sharing:group_detail", args=[self.group.pk]), {"username": "novachok"}
        )
        self.assertTrue(GroupMembership.objects.filter(group=self.group, user=newcomer).exists())

    def test_add_unknown_username_rejected(self):
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("sharing:group_detail", args=[self.group.pk]), {"username": "nemaie"}
        )
        self.assertContains(response, "не знайдено")

    def test_shared_with_me_lists_group_material(self):
        book = make_book(self.teacher, "Спільна книга")
        GroupAccess.objects.create(group=self.group, book=book, granted_by=self.teacher)

        self.client.force_login(self.student)
        response = self.client.get(reverse("book_library:shared_with_me"))
        self.assertContains(response, "Спільна книга")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ShareLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avtor", email="a@example.com", password="parol-parol-42"
        )
        self.book = make_book(self.user, "Відкрита книга")
        self.client.force_login(self.user)

    def test_link_list_empty_state(self):
        response = self.client.get(reverse("sharing:link_list"))
        self.assertContains(response, "Посилань немає")

    def test_create_link_generates_token(self):
        response = self.client.post(reverse("sharing:link_create"), {"book": self.book.pk})
        self.assertRedirects(response, reverse("sharing:link_list"))

        link = ShareLink.objects.get(book=self.book)
        self.assertTrue(link.token)
        self.assertEqual(link.created_by, self.user)

    def test_link_requires_exactly_one_target(self):
        response = self.client.post(reverse("sharing:link_create"), {})
        self.assertContains(response, "рівно одне")
        self.assertFalse(ShareLink.objects.exists())

    def test_link_list_renders_full_url(self):
        ShareLink.objects.create(book=self.book, token="abc123", created_by=self.user)
        response = self.client.get(reverse("sharing:link_list"))
        self.assertContains(response, reverse("sharing:share_view", args=["abc123"]))

    def test_share_view_public_for_anonymous(self):
        ShareLink.objects.create(book=self.book, token="publichnyi", created_by=self.user)
        self.client.logout()
        response = self.client.get(reverse("sharing:share_view", args=["publichnyi"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Відкрита книга")
        self.assertContains(response, "Створіть акаунт")

    def test_share_view_reports_expired(self):
        ShareLink.objects.create(
            book=self.book,
            token="prostrochenyi",
            created_by=self.user,
            expires_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(reverse("sharing:share_view", args=["prostrochenyi"]))
        self.assertContains(response, "Термін дії посилання минув")

    def test_share_view_reports_unknown_token(self):
        response = self.client.get(reverse("sharing:share_view", args=["nemaietakogo"]))
        self.assertContains(response, "не існує або було відкликане")

    def test_revoke_link_requires_post(self):
        link = ShareLink.objects.create(book=self.book, token="vidklyk", created_by=self.user)
        url = reverse("sharing:link_delete", args=[link.pk])

        self.client.get(url)
        self.assertTrue(ShareLink.objects.filter(pk=link.pk).exists())

        self.client.post(url)
        self.assertFalse(ShareLink.objects.filter(pk=link.pk).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class GroupAccessTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="vchytel2", email="v2@example.com", password="parol-parol-42",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="uchen2", email="u2@example.com", password="parol-parol-42"
        )
        self.group = Group.objects.create(owner=self.teacher, name="11-Б")
        GroupMembership.objects.create(group=self.group, user=self.student)
        self.book = make_book(self.teacher, "Геометрія")
        self.client.force_login(self.teacher)

    def test_form_renders_with_prefilled_group(self):
        response = self.client.get(f"{reverse('sharing:access_create')}?group={self.group.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["group"], str(self.group.pk))

    def test_grant_access_to_book(self):
        response = self.client.post(
            reverse("sharing:access_create"),
            {"group": self.group.pk, "book": self.book.pk, "permission": GroupAccess.Permission.VIEW},
        )
        self.assertRedirects(response, reverse("sharing:group_detail", args=[self.group.pk]))

        access = GroupAccess.objects.get(group=self.group, book=self.book)
        self.assertEqual(access.granted_by, self.teacher)
        self.assertEqual(access.permission, GroupAccess.Permission.VIEW)

    def test_granted_book_appears_in_student_shared_list(self):
        GroupAccess.objects.create(group=self.group, book=self.book, granted_by=self.teacher)

        self.client.force_login(self.student)
        response = self.client.get(reverse("book_library:shared_with_me"))
        self.assertContains(response, "Геометрія")

    def test_requires_exactly_one_target(self):
        for payload in [
            {"group": self.group.pk},
            {"group": self.group.pk, "book": self.book.pk, "folder": Folder.objects.create(
                owner=self.teacher, name="Тека").pk},
        ]:
            with self.subTest(payload=payload):
                response = self.client.post(reverse("sharing:access_create"), payload)
                self.assertContains(response, "рівно одне")
        self.assertFalse(GroupAccess.objects.exists())

    def test_duplicate_grant_rejected_with_form_error(self):
        GroupAccess.objects.create(group=self.group, book=self.book, granted_by=self.teacher)

        response = self.client.post(
            reverse("sharing:access_create"),
            {"group": self.group.pk, "book": self.book.pk, "permission": GroupAccess.Permission.EDIT},
        )
        self.assertContains(response, "уже має доступ")
        self.assertEqual(GroupAccess.objects.count(), 1)

    def test_cannot_grant_foreign_group_or_foreign_book(self):
        stranger = User.objects.create_user(
            username="chuzhak", email="ch@example.com", password="parol-parol-42"
        )
        foreign_group = Group.objects.create(owner=stranger, name="Чужа")
        foreign_book = make_book(stranger, "Чужа книга")

        response = self.client.post(
            reverse("sharing:access_create"),
            {"group": foreign_group.pk, "book": self.book.pk, "permission": "view"},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("sharing:access_create"),
            {"group": self.group.pk, "book": foreign_book.pk, "permission": "view"},
        )
        self.assertEqual(response.status_code, 200)

        self.assertFalse(GroupAccess.objects.exists())

    def test_revoke_requires_post_and_ownership(self):
        access = GroupAccess.objects.create(
            group=self.group, book=self.book, granted_by=self.teacher
        )
        url = reverse("sharing:access_delete", args=[access.pk])

        self.client.get(url)
        self.assertTrue(GroupAccess.objects.filter(pk=access.pk).exists())

        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url).status_code, 404)

        self.client.force_login(self.teacher)
        self.client.post(url)
        self.assertFalse(GroupAccess.objects.filter(pk=access.pk).exists())

    def test_group_detail_shows_grant_button_for_owner_only(self):
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertContains(response, "Надати доступ")

        self.client.force_login(self.student)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertNotContains(response, "Надати доступ")

    def test_book_detail_shows_grant_button(self):
        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        self.assertContains(response, "Надати групі")
