import atexit
import shutil
import tempfile
from datetime import timedelta

from unittest.mock import patch

from annotations.groups import create_note_group
from annotations.models import Annotation, Layer, LayerPublication, NoteGroup
from book_library.models import Book, Folder, ReadingProgress
from book_library.progress import record_progress
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
# Не tearDownModule: за --parallel на Linux (fork) теку ділять усі воркери, і той, що
# закінчив свій клас, видаляв її з-під інших. За fork atexit спрацьовує лише в батьківському процесі.
atexit.register(shutil.rmtree, TEST_MEDIA_ROOT, ignore_errors=True)


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
        self.assertEqual(data["user"], {"id": self.user.pk, "username": "uchen"})
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

    def test_password_change_revokes_issued_tokens(self):
        refresh = str(RefreshToken.for_user(self.user))
        access = str(AccessToken.for_user(self.user))
        self.user.set_unusable_password()
        self.user.save()

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(client.get(reverse("api:sync")).status_code, 401)

        # Refresh видає нову пару, але access несе старий хеш пароля — теж відкликаний.
        response = APIClient().post(reverse("api:auth_token_refresh"), {"refresh": refresh})
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json().get('access', '')}")
        self.assertEqual(client.get(reverse("api:sync")).status_code, 401)

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


class VersionPrefixTests(ApiTestCase):
    # Шляхи захардкоджені навмисно: reverse() пройшов би й після зміни префікса, встановлений застосунок ходить саме на цей рядок.
    def test_api_lives_under_v1(self):
        self.assertEqual(self.client.get("/api/v1/sync/").status_code, 200)

    def test_unversioned_path_is_gone(self):
        self.assertEqual(self.client.get("/api/sync/").status_code, 404)


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


class ProgressSyncTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Моя", page_count=20)
        self.foreign_book = make_book(self.teacher, "Чужа", page_count=20)

    def push(self, items):
        response = self.client.post(
            reverse("api:sync"), {"reading_progress": items}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def pull(self, since=None):
        params = {"since": since.isoformat()} if since else {}
        response = self.client.get(reverse("api:sync"), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["reading_progress"]

    def test_pull_full_and_delta(self):
        record_progress(self.user, self.book, 4)
        self.assertEqual([(p["book"], p["last_page"]) for p in self.pull()], [(self.book.pk, 4)])

        since = timezone.now()
        self.assertEqual(self.pull(since), [])
        record_progress(self.user, self.book, 9)
        self.assertEqual([p["last_page"] for p in self.pull(since)], [9])

    def test_pull_hides_progress_for_revoked_book(self):
        group = Group.objects.create(name="11-Г", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        access = GroupAccess.objects.create(
            group=group, book=self.foreign_book, granted_by=self.teacher
        )
        record_progress(self.user, self.foreign_book, 3)
        self.assertEqual([p["book"] for p in self.pull()], [self.foreign_book.pk])

        access.delete()
        self.assertEqual(self.pull(), [])

    def test_push_creates_progress(self):
        read_at = timezone.now() - timedelta(minutes=5)
        data = self.push([{"book": self.book.pk, "last_page": 12, "read_at": read_at.isoformat()}])
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["reading_progress"][0]["last_page"], 12)
        progress = ReadingProgress.objects.get(user=self.user, book=self.book)
        self.assertEqual(progress.last_page, 12)
        self.assertEqual(progress.read_at, read_at)

    def test_push_older_position_loses(self):
        record_progress(self.user, self.book, 15)
        stale = (timezone.now() - timedelta(days=1)).isoformat()
        data = self.push([{"book": self.book.pk, "last_page": 2, "read_at": stale}])
        self.assertEqual(data["reading_progress"][0]["last_page"], 15)
        self.assertEqual(ReadingProgress.objects.get(user=self.user, book=self.book).last_page, 15)

    def test_push_errors_are_per_item(self):
        now = timezone.now().isoformat()
        data = self.push(
            [
                {"book": self.foreign_book.pk, "last_page": 1, "read_at": now},
                {"book": self.book.pk, "last_page": 99, "read_at": now},
                {"book": self.book.pk, "last_page": 3, "read_at": "2026-09-18T10:00:00"},
                {"book": self.book.pk, "last_page": 3, "read_at": now},
            ]
        )
        self.assertEqual(len(data["errors"]), 3)
        self.assertTrue(all(e["op"] == "progress" for e in data["errors"]))
        self.assertEqual([p["last_page"] for p in data["reading_progress"]], [3])

    def test_malformed_section_is_400(self):
        response = self.client.post(reverse("api:sync"), {"reading_progress": {}}, format="json")
        self.assertEqual(response.status_code, 400)


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
        self.assertEqual(tombstone.layer_id, layer.pk)

    def test_book_delete_cascade_does_not_crash(self):
        user = User.objects.create_user(username="u", email="u@example.com", password="parol-parol-42")
        book = make_book(user, "Книга")
        layer = Layer.objects.create(book=book, owner=user)
        Annotation.objects.create(layer=layer, page_number=1, coords=RECT)
        book.delete()
        self.assertFalse(Annotation.objects.exists())


class SharedLayerSyncTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name="9-А", owner=self.teacher)
        self.membership = GroupMembership.objects.create(group=self.group, user=self.user)
        self.book = make_book(self.teacher, "Географія")
        GroupAccess.objects.create(group=self.group, book=self.book, granted_by=self.teacher)
        self.old = self.annotate(self.book, owner=self.teacher, note_text="давня")
        self.layer = self.old.layer

    def publish(self):
        return LayerPublication.objects.create(layer=self.layer, group=self.group)

    def pull(self, since=None):
        params = {"since": since.isoformat()} if since else {}
        response = self.client.get(reverse("api:sync"), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_full_pull_includes_published_layer(self):
        self.publish()
        data = self.pull()
        self.assertEqual(
            data["shared_layers"],
            [{"id": self.layer.pk, "book": self.book.pk, "owner": self.teacher.pk,
              "owner_username": "vchytel"}],
        )
        self.assertEqual([a["id"] for a in data["shared_annotations"]], [self.old.pk])
        self.assertEqual(data["shared_annotations"][0]["layer"], self.layer.pk)
        # Власні анотації окремо: чужі туди не домішуються.
        self.assertEqual(data["annotations"], [])

    def test_unpublished_layer_is_not_sent(self):
        data = self.pull()
        self.assertEqual(data["shared_layers"], [])
        self.assertEqual(data["shared_annotations"], [])

    def test_delta_sends_only_changed_annotations(self):
        self.publish()
        since = timezone.now()
        fresh = self.annotate(self.book, owner=self.teacher, note_text="нова")
        data = self.pull(since)
        self.assertEqual([a["id"] for a in data["shared_annotations"]], [fresh.pk])

    def test_delta_after_publication_sends_whole_layer(self):
        since = timezone.now()
        self.publish()
        data = self.pull(since)
        self.assertEqual([a["id"] for a in data["shared_annotations"]], [self.old.pk])

    def test_delta_after_joining_group_sends_whole_layer(self):
        self.publish()
        self.membership.delete()
        since = timezone.now()
        GroupMembership.objects.create(group=self.group, user=self.user)
        data = self.pull(since)
        self.assertEqual([a["id"] for a in data["shared_annotations"]], [self.old.pk])

    def test_delta_reports_teacher_deletions_in_shared_layer_only(self):
        self.publish()
        private_book = make_book(self.teacher, "Особиста")
        private = self.annotate(private_book, owner=self.teacher)
        since = timezone.now()
        old_pk, private_pk = self.old.pk, private.pk
        self.old.delete()
        private.delete()

        data = self.pull(since)
        self.assertEqual(data["deleted_annotation_ids"], [old_pk])
        self.assertNotIn(private_pk, data["deleted_annotation_ids"])

    def test_unpublishing_drops_layer_from_list(self):
        publication = self.publish()
        self.assertEqual(len(self.pull()["shared_layers"]), 1)
        publication.delete()
        self.assertEqual(self.pull()["shared_layers"], [])

    def test_push_cannot_touch_teacher_annotation(self):
        self.publish()
        response = self.client.post(
            reverse("api:sync"), {"annotations": {"delete": [self.old.pk]}}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Annotation.objects.filter(pk=self.old.pk).exists())


class NoteGroupSyncTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Моя", page_count=5)
        self.other_book = make_book(self.user, "Друга", page_count=5)
        self.group = create_note_group(self.book, self.user, "Формули")
        self.note = self.annotate(self.book, group=self.group)

    def pull(self, since=None):
        params = {"since": since.isoformat()} if since else {}
        response = self.client.get(reverse("api:sync"), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def push(self, payload):
        response = self.client.post(reverse("api:sync"), payload, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_pull_lists_groups_and_annotation_group(self):
        data = self.pull()
        self.assertEqual(
            [(g["id"], g["book"], g["name"]) for g in data["note_groups"]],
            [(self.group.pk, self.book.pk, "Формули")],
        )
        self.assertEqual(data["annotations"][0]["group"], self.group.pk)

    def test_pull_lists_groups_fully_even_in_delta(self):
        data = self.pull(timezone.now())
        self.assertEqual([g["id"] for g in data["note_groups"]], [self.group.pk])

    def test_shared_annotations_have_no_group(self):
        group = Group.objects.create(name="9-А", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        book = make_book(self.teacher, "Вчительська")
        GroupAccess.objects.create(group=group, book=book, granted_by=self.teacher)
        teacher_group = create_note_group(book, self.teacher, "Відповіді")
        shared = self.annotate(book, owner=self.teacher, group=teacher_group)
        LayerPublication.objects.create(layer=shared.layer, group=group)

        notes = self.pull()["shared_annotations"]
        self.assertEqual([n["id"] for n in notes], [shared.pk])
        self.assertNotIn("group", notes[0])

    def test_push_creates_group_and_annotation_into_it(self):
        data = self.push(
            {
                "note_groups": {"create": [{"client_id": "t1", "book": self.book.pk, "name": "Офлайн"}]},
                "annotations": {
                    "create": [
                        {"client_id": "a1", "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "t1"}
                    ]
                },
            }
        )
        self.assertEqual(data["errors"], [])
        created_group = data["note_groups"]["created"][0]
        self.assertEqual(created_group["client_id"], "t1")
        annotation = Annotation.objects.get(pk=data["created"][0]["id"])
        self.assertEqual(annotation.group_id, created_group["id"])

    def test_push_create_existing_name_reuses_topic(self):
        # Та сама тема, створена офлайн на іншому пристрої (або повторений пуш):
        # нотатки мають потрапити в наявну тему, а не загубитися в errors.
        data = self.push(
            {
                "note_groups": {"create": [{"client_id": "t1", "book": self.book.pk, "name": " Формули "}]},
                "annotations": {
                    "create": [
                        {"client_id": "a1", "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "t1"}
                    ]
                },
            }
        )
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["note_groups"]["created"][0]["id"], self.group.pk)
        self.assertEqual(data["note_groups"]["created"][0]["client_id"], "t1")
        self.assertEqual(NoteGroup.objects.filter(name="Формули").count(), 1)
        annotation = Annotation.objects.get(pk=data["created"][0]["id"])
        self.assertEqual(annotation.group, self.group)

    def new_note_into(self, client_id):
        return {"client_id": "a1", "book": self.book.pk, "page_number": 1,
                "coords": RECT, "color": "#FFEB3B", "group_client_id": client_id}

    def test_push_rename_then_create_same_name(self):
        # Офлайн: «Формули» → «Старі формули», потім нова «Формули» з новою нотаткою.
        data = self.push(
            {
                "note_groups": {
                    "update": [{"id": self.group.pk, "name": "Старі формули"}],
                    "create": [{"client_id": "t1", "book": self.book.pk, "name": "Формули"}],
                },
                "annotations": {"create": [self.new_note_into("t1")]},
            }
        )
        self.assertEqual(data["errors"], [])
        new_id = data["note_groups"]["created"][0]["id"]
        self.assertNotEqual(new_id, self.group.pk)
        self.assertEqual(Annotation.objects.get(pk=data["created"][0]["id"]).group_id, new_id)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Старі формули")

    def test_push_delete_then_recreate_same_name(self):
        data = self.push(
            {
                "note_groups": {
                    "delete": [self.group.pk],
                    "create": [{"client_id": "t1", "book": self.book.pk, "name": "Формули"}],
                },
                "annotations": {"create": [self.new_note_into("t1")]},
            }
        )
        self.assertEqual(data["errors"], [])
        new_group = NoteGroup.objects.get(name="Формули")
        self.assertNotEqual(new_group.pk, self.group.pk)
        self.assertEqual(Annotation.objects.get(pk=data["created"][0]["id"]).group, new_group)
        self.note.refresh_from_db()
        self.assertIsNone(self.note.group)

    def test_push_update_into_group_deleted_in_same_push_keeps_other_fields(self):
        # Клієнт переніс нотатку в тему, а потім видалив тему: текст не має загубитися.
        data = self.push(
            {
                "note_groups": {"delete": [self.group.pk]},
                "annotations": {
                    "update": [{"id": self.note.pk, "group": self.group.pk, "note_text": "збережено"}]
                },
            }
        )
        self.assertEqual(data["errors"], [])
        self.note.refresh_from_db()
        self.assertEqual(self.note.note_text, "збережено")
        self.assertIsNone(self.note.group)

    def test_push_group_client_id_of_other_book_or_unknown_is_error(self):
        data = self.push(
            {
                "note_groups": {"create": [{"client_id": "t1", "book": self.other_book.pk, "name": "Інша"}]},
                "annotations": {
                    "create": [
                        {"client_id": "a1", "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "t1"},
                        {"client_id": "a2", "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "nema"},
                    ]
                },
            }
        )
        self.assertEqual([e["client_id"] for e in data["errors"]], ["a1", "a2"])
        self.assertEqual(Annotation.objects.count(), 1)  # лише self.note

    def test_push_update_annotation_group(self):
        second = create_note_group(self.book, self.user, "Задачі")
        foreign = create_note_group(self.book, self.teacher, "Чужа")
        data = self.push(
            {"annotations": {"update": [{"id": self.note.pk, "group": second.pk}]}}
        )
        self.assertEqual(data["updated"][0]["group"], second.pk)

        data = self.push(
            {"annotations": {"update": [{"id": self.note.pk, "group": foreign.pk}]}}
        )
        self.assertEqual(data["errors"][0]["op"], "update")
        self.note.refresh_from_db()
        self.assertEqual(self.note.group, second)

    def test_push_rename_duplicate_is_error_not_500(self):
        create_note_group(self.book, self.user, "Задачі")
        data = self.push(
            {
                "note_groups": {
                    "update": [{"id": self.group.pk, "name": "Задачі"}],
                    "create": [{"client_id": "t1", "book": self.book.pk, "name": "Нова"}],
                }
            }
        )
        self.assertEqual([e["op"] for e in data["errors"]], ["group_update"])
        self.assertTrue(NoteGroup.objects.filter(name="Нова").exists())

    def test_push_delete_group_frees_annotations_in_delta(self):
        since = timezone.now()
        data = self.push({"note_groups": {"delete": [self.group.pk]}})
        self.assertEqual(data["note_groups"]["deleted"], [self.group.pk])

        pulled = self.pull(since)
        self.assertEqual(pulled["note_groups"], [])
        self.assertEqual([(a["id"], a["group"]) for a in pulled["annotations"]], [(self.note.pk, None)])

    def test_push_cannot_touch_foreign_group(self):
        foreign = create_note_group(self.book, self.teacher, "Чужа")
        data = self.push(
            {"note_groups": {"update": [{"id": foreign.pk, "name": "Зламано"}], "delete": [foreign.pk]}}
        )
        self.assertEqual([e["op"] for e in data["errors"]], ["group_update"])
        self.assertTrue(NoteGroup.objects.filter(pk=foreign.pk, name="Чужа").exists())

    def test_group_ops_count_toward_batch_limit(self):
        with patch("api.sync.MAX_BATCH_OPS", 1):
            response = self.client.post(
                reverse("api:sync"),
                {"note_groups": {"create": [{"client_id": "t1", "book": self.book.pk, "name": "А"},
                                            {"client_id": "t2", "book": self.book.pk, "name": "Б"}]}},
                format="json",
            )
        self.assertEqual(response.status_code, 400)

    def test_bad_note_groups_section_is_400(self):
        response = self.client.post(reverse("api:sync"), {"note_groups": []}, format="json")
        self.assertEqual(response.status_code, 400)
