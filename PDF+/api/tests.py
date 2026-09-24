import shutil
import tempfile

from annotations.models import Annotation, Layer
from book_library.models import Book, Folder
from book_sharing_settings.testing import FILESYSTEM_STORAGES
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from sharing.models import Group, GroupAccess, GroupMembership
from users.models import User

from .models import DeletedAnnotation

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-api-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


def make_book(owner, title, folder=None, page_count=10):
    return Book.objects.create(
        owner=owner,
        title=title,
        folder=folder,
        page_count=page_count,
        file=SimpleUploadedFile("b.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    )


RECT = [{"x0": 1, "y0": 2, "x1": 30, "y1": 40}]


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES, API_SYNC_OVERLAP_SECONDS=0
)
class ApiTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uchen", email="uchen@example.com", password="parol-parol-42"
        )
        self.teacher = User.objects.create_user(
            username="vchytel",
            email="vchytel@example.com",
            password="parol-parol-42",
            role=User.Role.TEACHER,
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")

    def annotate(self, book, owner=None, **fields):
        layer, _ = Layer.objects.get_or_create(book=book, owner=owner or self.user)
        defaults = {"page_number": 1, "coords": RECT, "color": "#FFEB3B"}
        return Annotation.objects.create(layer=layer, **{**defaults, **fields})


class TokenTests(ApiTestCase):
    def test_exchanges_credentials_for_token_pair(self):
        response = APIClient().post(
            reverse("api:auth_token"), {"username": "uchen", "password": "parol-parol-42"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["user"], {"id": self.user.pk, "username": "uchen", "role": "student"})
        self.assertEqual(AccessToken(data["access"])["user_id"], str(self.user.pk))
        self.assertEqual(RefreshToken(data["refresh"])["user_id"], str(self.user.pk))

    def test_access_token_authenticates_without_db_token_table(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        self.assertEqual(client.get(reverse("api:sync")).status_code, 200)

    def test_refresh_returns_new_pair(self):
        refresh = str(RefreshToken.for_user(self.user))
        response = APIClient().post(reverse("api:auth_token_refresh"), {"refresh": refresh})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(AccessToken(data["access"])["user_id"], str(self.user.pk))
        self.assertNotEqual(data["refresh"], refresh)

    def test_wrong_password_rejected(self):
        response = APIClient().post(
            reverse("api:auth_token"), {"username": "uchen", "password": "ne-toi"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.json())

    def test_tampered_token_rejected(self):
        token = str(AccessToken.for_user(self.user))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token[:-4]}xxxx")
        self.assertEqual(client.get(reverse("api:sync")).status_code, 401)

    def test_endpoints_require_token(self):
        anonymous = APIClient()
        for name in ("catalog_books", "sync"):
            self.assertEqual(anonymous.get(reverse(f"api:{name}")).status_code, 401)


class CatalogTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.catalog = Folder.objects.create(owner=self.teacher, name="Каталог", is_catalog=True)
        self.sub = Folder.objects.create(owner=self.teacher, name="Біологія", parent=self.catalog)
        self.in_catalog = make_book(self.teacher, "Біологія 11", folder=self.sub)
        self.private = make_book(self.teacher, "Приватна", folder=None)

    def test_lists_only_catalog_books_with_folder_path(self):
        response = self.client.get(reverse("api:catalog_books"))
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual([b["id"] for b in results], [self.in_catalog.pk])
        self.assertEqual(results[0]["folder_path"], "Каталог / Біологія")

    def test_search_is_case_insensitive_cyrillic(self):
        make_book(self.teacher, "Хімія 10", folder=self.sub)
        response = self.client.get(reverse("api:catalog_books"), {"search": "хім"})
        self.assertEqual([b["title"] for b in response.json()["results"]], ["Хімія 10"])


class BookFileTests(ApiTestCase):
    def test_redirects_to_storage_url_for_visible_book(self):
        book = make_book(self.user, "Моя")
        response = self.client.get(reverse("api:book_file", args=[book.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], book.file.url)

    def test_hidden_book_is_404(self):
        book = make_book(self.teacher, "Чужа")
        self.assertEqual(self.client.get(reverse("api:book_file", args=[book.pk])).status_code, 404)


class SyncPullTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.own_folder = Folder.objects.create(owner=self.user, name="Моя тека")
        self.own_book = make_book(self.user, "Моя", folder=self.own_folder)
        self.foreign_book = make_book(self.teacher, "Чужа")
        self.annotation = self.annotate(self.own_book, note_text="нотатка")

    def pull(self, since=None):
        params = {"since": since.isoformat()} if since else {}
        response = self.client.get(reverse("api:sync"), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_full_state_without_since(self):
        data = self.pull()
        self.assertEqual([b["id"] for b in data["books"]], [self.own_book.pk])
        self.assertEqual([f["id"] for f in data["folders"]], [self.own_folder.pk])
        self.assertEqual([a["id"] for a in data["annotations"]], [self.annotation.pk])
        self.assertEqual(data["annotations"][0]["book"], self.own_book.pk)
        self.assertEqual(data["visible_book_ids"], [self.own_book.pk])
        self.assertEqual(data["deleted_annotation_ids"], [])
        self.assertIn("server_time", data)

    def test_delta_returns_only_changes_after_since(self):
        since = timezone.now()
        new_book = make_book(self.user, "Нова")
        self.annotation.note_text = "змінено"
        self.annotation.save()

        data = self.pull(since)
        self.assertEqual([b["id"] for b in data["books"]], [new_book.pk])
        self.assertEqual(data["folders"], [])
        self.assertEqual([a["id"] for a in data["annotations"]], [self.annotation.pk])
        self.assertEqual(data["visible_book_ids"], sorted([self.own_book.pk, new_book.pk]))

    def test_delta_includes_books_granted_via_group_after_since(self):
        group = Group.objects.create(name="11-А", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        since = timezone.now()
        GroupAccess.objects.create(group=group, book=self.foreign_book, granted_by=self.teacher)

        data = self.pull(since)
        self.assertEqual([b["id"] for b in data["books"]], [self.foreign_book.pk])

    def test_delta_includes_folder_subtree_after_joining_group(self):
        folder = Folder.objects.create(owner=self.teacher, name="Клас")
        child = Folder.objects.create(owner=self.teacher, name="Вкладена", parent=folder)
        book = make_book(self.teacher, "У вкладеній", folder=child)
        group = Group.objects.create(name="11-Б", owner=self.teacher)
        GroupAccess.objects.create(group=group, folder=folder, granted_by=self.teacher)
        since = timezone.now()
        GroupMembership.objects.create(group=group, user=self.user)

        data = self.pull(since)
        self.assertEqual(sorted(f["id"] for f in data["folders"]), sorted([folder.pk, child.pk]))
        self.assertEqual([b["id"] for b in data["books"]], [book.pk])

    def test_delta_reports_deleted_annotations(self):
        since = timezone.now()
        pk = self.annotation.pk
        self.annotation.delete()

        data = self.pull(since)
        self.assertEqual(data["deleted_annotation_ids"], [pk])
        self.assertEqual(data["annotations"], [])

    def test_revoked_book_drops_out_of_visible_ids(self):
        group = Group.objects.create(name="11-В", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        access = GroupAccess.objects.create(group=group, book=self.foreign_book, granted_by=self.teacher)
        self.assertIn(self.foreign_book.pk, self.pull()["visible_book_ids"])
        access.delete()
        self.assertNotIn(self.foreign_book.pk, self.pull()["visible_book_ids"])

    def test_bad_since_is_400(self):
        response = self.client.get(reverse("api:sync"), {"since": "вчора"})
        self.assertEqual(response.status_code, 400)
        naive = self.client.get(reverse("api:sync"), {"since": "2026-09-18T10:00:00"})
        self.assertEqual(naive.status_code, 400)


class SyncPushTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Моя", page_count=5)
        self.foreign_book = make_book(self.teacher, "Чужа")

    def push(self, annotations):
        response = self.client.post(reverse("api:sync"), {"annotations": annotations}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_create_maps_client_id_to_server_id(self):
        data = self.push(
            {
                "create": [
                    {
                        "client_id": "c-1",
                        "book": self.book.pk,
                        "page_number": 2,
                        "coords": RECT,
                        "color": "#a5d6a7",
                        "note_text": "з телефона",
                    }
                ]
            }
        )
        created = data["created"][0]
        annotation = Annotation.objects.get(pk=created["id"])
        self.assertEqual(created["client_id"], "c-1")
        self.assertEqual(annotation.layer.owner, self.user)
        self.assertEqual(annotation.color, "#A5D6A7")
        self.assertEqual(data["errors"], [])

    def test_update_and_delete(self):
        target = self.annotate(self.book)
        victim = self.annotate(self.book)
        data = self.push(
            {"update": [{"id": target.pk, "note_text": "оновлено"}], "delete": [victim.pk, 999999]}
        )
        target.refresh_from_db()
        self.assertEqual(target.note_text, "оновлено")
        self.assertFalse(Annotation.objects.filter(pk=victim.pk).exists())
        self.assertEqual(data["deleted"], [victim.pk, 999999])
        self.assertEqual(DeletedAnnotation.objects.filter(annotation_id=victim.pk).count(), 1)

    def test_per_item_errors_do_not_block_batch(self):
        foreign = self.annotate(self.foreign_book, owner=self.teacher)
        data = self.push(
            {
                "create": [
                    {"client_id": "bad-book", "book": self.foreign_book.pk, "page_number": 1,
                     "coords": RECT, "color": "#FFEB3B"},
                    {"client_id": "bad-page", "book": self.book.pk, "page_number": 99,
                     "coords": RECT, "color": "#FFEB3B"},
                    {"client_id": "ok", "book": self.book.pk, "page_number": 1,
                     "coords": RECT, "color": "#FFEB3B"},
                ],
                "update": [{"id": foreign.pk, "note_text": "чуже"}],
            }
        )
        self.assertEqual([c["client_id"] for c in data["created"]], ["ok"])
        self.assertEqual(
            {str(e.get("client_id") or e.get("id")) for e in data["errors"]},
            {"bad-book", "bad-page", str(foreign.pk)},
        )
        foreign.refresh_from_db()
        self.assertEqual(foreign.note_text, "")

    def test_malformed_batch_is_400(self):
        response = self.client.post(reverse("api:sync"), {"annotations": {"create": "x"}}, format="json")
        self.assertEqual(response.status_code, 400)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class TombstoneTests(TestCase):
    def test_web_api_delete_records_tombstone(self):
        user = User.objects.create_user(username="u", email="u@example.com", password="parol-parol-42")
        book = make_book(user, "Книга")
        layer = Layer.objects.create(book=book, owner=user)
        annotation = Annotation.objects.create(layer=layer, page_number=1, coords=RECT)
        self.client.force_login(user)
        self.client.delete(reverse("annotations:api_annotation_detail", args=[annotation.pk]))
        tombstone = DeletedAnnotation.objects.get(annotation_id=annotation.pk)
        self.assertEqual(tombstone.owner, user)

    def test_book_delete_cascade_does_not_crash(self):
        user = User.objects.create_user(username="u", email="u@example.com", password="parol-parol-42")
        book = make_book(user, "Книга")
        layer = Layer.objects.create(book=book, owner=user)
        Annotation.objects.create(layer=layer, page_number=1, coords=RECT)
        book.delete()
        self.assertFalse(Annotation.objects.exists())
