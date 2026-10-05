from datetime import timedelta

from annotations.models import Layer, NoteGroup
from book_library.models import Book, Folder
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
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

from book_sharing_settings.testing import FILESYSTEM_STORAGES
from django.test import override_settings

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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

    def test_add_member_button_is_outline_only(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertContains(response, 'class="btn btn-outline-primary mt-2"')
        self.assertNotContains(response, "btn-primary btn btn-outline-primary")

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


class GroupInviteTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="vchytelka", email="v@example.com", password="parol-parol-42",
            role=User.Role.TEACHER,
        )
        self.newcomer = User.objects.create_user(
            username="novachok", email="n@example.com", password="parol-parol-42"
        )
        self.group = Group.objects.create(owner=self.teacher, name="10-А", invite_token="zaproshennia")
        self.join_url = reverse("sharing:group_join", args=["zaproshennia"])

    def test_group_create_generates_invite_token(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("sharing:group_create"), {"name": "11-Б"})
        group = Group.objects.get(name="11-Б")
        self.assertTrue(group.invite_token)

    def test_owner_sees_invite_url_on_detail(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertContains(response, self.join_url)
        self.assertContains(response, "Посилання для приєднання")

    def test_member_does_not_see_invite_url(self):
        GroupMembership.objects.create(group=self.group, user=self.newcomer)
        self.client.force_login(self.newcomer)
        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertNotContains(response, self.join_url)

    def test_join_get_shows_confirmation_without_joining(self):
        self.client.force_login(self.newcomer)
        response = self.client.get(self.join_url)
        self.assertContains(response, "10-А")
        self.assertContains(response, "Приєднатися до групи")
        self.assertFalse(GroupMembership.objects.filter(group=self.group, user=self.newcomer).exists())

    def test_join_post_creates_membership(self):
        self.client.force_login(self.newcomer)
        response = self.client.post(self.join_url)
        self.assertRedirects(response, reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertTrue(GroupMembership.objects.filter(group=self.group, user=self.newcomer).exists())

    def test_join_twice_does_not_duplicate(self):
        self.client.force_login(self.newcomer)
        self.client.post(self.join_url)
        self.client.post(self.join_url)
        self.assertEqual(GroupMembership.objects.filter(group=self.group, user=self.newcomer).count(), 1)

    def test_owner_cannot_join_own_group(self):
        self.client.force_login(self.teacher)
        self.client.post(self.join_url)
        self.assertFalse(GroupMembership.objects.filter(group=self.group, user=self.teacher).exists())

    def test_anonymous_sees_login_and_register_with_next(self):
        response = self.client.get(self.join_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10-А")
        self.assertContains(response, f"{reverse('accounts:login')}?next={self.join_url}")
        self.assertContains(response, f"{reverse('accounts:register')}?next={self.join_url}")

    def test_unknown_token_404(self):
        self.client.force_login(self.newcomer)
        response = self.client.get(reverse("sharing:group_join", args=["nemaie"]))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Посилання недоступне", status_code=404)

    def test_regenerate_invalidates_old_token(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("sharing:group_invite", args=[self.group.pk]), {"action": "regenerate"})
        self.group.refresh_from_db()
        self.assertTrue(self.group.invite_token)
        self.assertNotEqual(self.group.invite_token, "zaproshennia")

        self.client.force_login(self.newcomer)
        response = self.client.post(self.join_url)
        self.assertEqual(response.status_code, 404)

    def test_disable_removes_token(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("sharing:group_invite", args=[self.group.pk]), {"action": "disable"})
        self.group.refresh_from_db()
        self.assertIsNone(self.group.invite_token)

        response = self.client.get(reverse("sharing:group_detail", args=[self.group.pk]))
        self.assertContains(response, "Створити посилання")

    def test_non_owner_cannot_manage_invite(self):
        self.client.force_login(self.newcomer)
        response = self.client.post(
            reverse("sharing:group_invite", args=[self.group.pk]), {"action": "disable"}
        )
        self.assertEqual(response.status_code, 404)
        self.group.refresh_from_db()
        self.assertEqual(self.group.invite_token, "zaproshennia")

    def test_register_with_next_returns_to_join_page(self):
        response = self.client.post(
            f"{reverse('accounts:register')}?next={self.join_url}",
            {
                "username": "marichka",
                "email": "marichka@example.com",
                "role": User.Role.STUDENT,
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, self.join_url)

    def test_register_ignores_external_next(self):
        response = self.client.post(
            f"{reverse('accounts:register')}?next=https://evil.example/",
            {
                "username": "marichka",
                "email": "marichka@example.com",
                "role": User.Role.STUDENT,
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, reverse("book_library:home"))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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

    def test_link_without_annotations_by_default(self):
        self.client.post(reverse("sharing:link_create"), {"book": self.book.pk})
        self.assertFalse(ShareLink.objects.get(book=self.book).include_annotations)

    def test_create_link_with_annotations(self):
        self.client.post(
            reverse("sharing:link_create"), {"book": self.book.pk, "include_annotations": "True"}
        )
        link = ShareLink.objects.get(book=self.book)
        self.assertTrue(link.include_annotations)

        response = self.client.get(reverse("sharing:link_list"))
        self.assertContains(response, "з нотатками")

    def test_create_form_preselects_annotations_from_query(self):
        url = reverse("sharing:link_create")
        response = self.client.get(f"{url}?book={self.book.pk}&annotations=1")
        self.assertTrue(response.context["form"].initial["include_annotations"])

        response = self.client.get(f"{url}?book={self.book.pk}")
        self.assertFalse(response.context["form"].initial["include_annotations"])

    def test_link_requires_exactly_one_target(self):
        response = self.client.post(reverse("sharing:link_create"), {})
        self.assertContains(response, "рівно одне")
        self.assertFalse(ShareLink.objects.exists())

    def test_link_rejects_past_expiry(self):
        yesterday = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            reverse("sharing:link_create"), {"book": self.book.pk, "expires_at": yesterday}
        )
        self.assertContains(response, "не може бути в минулому")
        self.assertFalse(ShareLink.objects.exists())

    def test_link_accepts_future_expiry(self):
        tomorrow = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            reverse("sharing:link_create"), {"book": self.book.pk, "expires_at": tomorrow}
        )
        self.assertRedirects(response, reverse("sharing:link_list"))
        self.assertTrue(ShareLink.objects.filter(book=self.book).exists())

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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class ShareLinkTopicFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avtor-tem", email="at@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = make_book(self.user, "Фізика")
        self.other_book = make_book(self.user, "Хімія")
        layer = Layer.objects.create(book=self.book, owner=self.user)
        self.formulas = NoteGroup.objects.create(layer=layer, name="Формули")
        self.answers = NoteGroup.objects.create(layer=layer, name="Відповіді")
        other_layer = Layer.objects.create(book=self.other_book, owner=self.user)
        self.other_topic = NoteGroup.objects.create(layer=other_layer, name="Реакції")
        self.url = reverse("sharing:link_create")

    def post(self, **data):
        return self.client.post(self.url, {"book": self.book.pk, **data})

    def test_selected_topics_saved_with_selected_scope(self):
        response = self.post(include_annotations="True", note_groups=[self.formulas.pk])
        self.assertRedirects(response, reverse("sharing:link_list"))
        link = ShareLink.objects.get()
        self.assertEqual(link.annotation_scope, ShareLink.AnnotationScope.SELECTED)
        self.assertEqual(list(link.note_groups.all()), [self.formulas])

    def test_no_topics_means_all(self):
        self.post(include_annotations="True")
        link = ShareLink.objects.get()
        self.assertEqual(link.annotation_scope, ShareLink.AnnotationScope.ALL)
        self.assertFalse(link.note_groups.exists())

    def test_topic_of_other_book_rejected(self):
        response = self.post(include_annotations="True", note_groups=[self.other_topic.pk])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Обрані теми мають належати обраній книзі.")
        self.assertFalse(ShareLink.objects.exists())

    def test_foreign_topic_rejected(self):
        stranger = User.objects.create_user(
            username="chuzhyi", email="c@example.com", password="parol-parol-42"
        )
        foreign_layer = Layer.objects.create(book=self.book, owner=stranger)
        foreign = NoteGroup.objects.create(layer=foreign_layer, name="Чужа")
        response = self.post(include_annotations="True", note_groups=[foreign.pk])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShareLink.objects.exists())

    def test_topics_need_annotations_and_book(self):
        response = self.post(note_groups=[self.formulas.pk])
        self.assertContains(response, "Теми можна обрати лише для посилання на книгу з нотатками.")

        folder = Folder.objects.create(owner=self.user, name="Тека")
        response = self.client.post(
            self.url,
            {"folder": folder.pk, "include_annotations": "True", "note_groups": [self.formulas.pk]},
        )
        self.assertContains(response, "Теми можна обрати лише для посилання на книгу з нотатками.")
        self.assertFalse(ShareLink.objects.exists())

    def test_form_marks_topics_with_book(self):
        response = self.client.get(self.url)
        self.assertContains(response, f'data-book="{self.book.pk}"')
        self.assertContains(response, "Фізика — Формули")

    def test_form_hides_topics_field_when_user_has_none(self):
        NoteGroup.objects.all().delete()
        response = self.client.get(self.url)
        self.assertNotIn("note_groups", response.context["form"].fields)

    def test_link_list_shows_topics(self):
        self.post(include_annotations="True", note_groups=[self.formulas.pk, self.answers.pk])
        response = self.client.get(reverse("sharing:link_list"))
        self.assertContains(response, "Відповіді, Формули")

        NoteGroup.objects.all().delete()
        response = self.client.get(reverse("sharing:link_list"))
        self.assertContains(response, "теми видалено")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class ShareLinkAdminTopicsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="adminka", email="adm@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.admin)
        book = make_book(self.admin, "Фізика")
        layer = Layer.objects.create(book=book, owner=self.admin)
        self.topic = NoteGroup.objects.create(layer=layer, name="Формули")
        self.link = ShareLink.objects.create(
            book=book, token="adm-tema", created_by=self.admin, include_annotations=True,
            annotation_scope=ShareLink.AnnotationScope.SELECTED,
        )
        self.link.note_groups.set([self.topic])

    def test_change_page_shows_topics_read_only(self):
        response = self.client.get(reverse("admin:sharing_sharelink_change", args=[self.link.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Фізика — Формули")
        self.assertNotContains(response, 'name="annotation_scope"')
        self.assertNotContains(response, 'name="note_groups"')
