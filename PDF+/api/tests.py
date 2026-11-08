import atexit
import os
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path

from unittest.mock import patch

from annotations.groups import create_note_group
from annotations.models import Annotation, Layer, LayerPublication, NoteGroup
from book_library.models import Book, Folder, ReadingProgress
from book_library.progress import record_progress
from book_sharing_settings.testing import FILESYSTEM_STORAGES
import pymupdf
import yaml
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from google.auth.exceptions import TransportError
from jsonschema import Draft202012Validator
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from sharing.models import Group, GroupAccess, GroupInvitation, GroupMembership, ShareLink
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
# client_id нотаток у пуші — UUID, згенерований клієнтом.
A1 = "0b7e1d2c-3f4a-4b5c-8d6e-7f8091a2b3c4"
A2 = "1c8f2e3d-4a5b-4c6d-9e7f-8091a2b3c4d5"
A3 = "2d903f4e-5b6c-4d7e-af80-91a2b3c4d5e6"


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES, API_SYNC_OVERLAP_SECONDS=0
)
class ApiTestCase(TestCase):
    def setUp(self):
        # Ліміт auth_token спільний для входу, refresh і виходу, а кеш живе між тестами.
        cache.clear()
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


class AuthThrottleTests(ApiTestCase):
    """Ліміт входу рахується за IP клієнта з CF-Connecting-IP, а не за X-Forwarded-For."""

    def login(self, ip=None, **headers):
        if ip:
            headers["HTTP_CF_CONNECTING_IP"] = ip
        return APIClient().post(
            reverse("api:auth_token"), {"username": "uchen", "password": "ne-toi"}, **headers
        ).status_code

    def test_spoofed_forwarded_for_does_not_reset_limit(self):
        # Cloudflare дописує справжній IP у кінець XFF клієнта — префікс підробляється.
        statuses = [
            self.login("203.0.113.7", HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.7")
            for i in range(11)
        ]
        self.assertEqual(statuses[:10], [401] * 10)
        self.assertEqual(statuses[-1], 429)

    def test_each_client_ip_has_own_limit(self):
        for _ in range(10):
            self.login("203.0.113.7")
        self.assertEqual(self.login("203.0.113.7"), 429)
        self.assertEqual(self.login("203.0.113.8"), 401)

    def test_ipv6_addresses_share_limit_per_64(self):
        for i in range(10):
            self.login(f"2001:db8:aa:bb::{i + 1}")
        self.assertEqual(self.login("2001:db8:aa:bb:ffff::1"), 429)
        self.assertEqual(self.login("2001:db8:aa:cc::1"), 401)

    def test_without_cloudflare_header_falls_back_to_remote_addr(self):
        # Локально й у тестах заголовка нема: ключ — REMOTE_ADDR (127.0.0.1).
        statuses = [self.login() for _ in range(11)]
        self.assertEqual(statuses[-1], 429)


GOOGLE_CLAIMS = {
    "email": "zhertva@example.com",
    "email_verified": True,
    "given_name": "Zhertva",
    "aud": "web-client-id",
}


@override_settings(GOOGLE_TOKEN_AUDIENCES=["web-client-id", "ios-client-id"])
@patch("api.views.id_token.verify_oauth2_token")
class GoogleTokenTests(ApiTestCase):
    """Акаунт шукається за поштою — ті самі перевірки, що й у веб-вході через Google."""

    def exchange(self, verify, claims=None, token="google-id-token"):
        verify.return_value = {**GOOGLE_CLAIMS, **(claims or {})}
        return APIClient().post(reverse("api:auth_google"), {"id_token": token}, format="json")

    def test_new_user_gets_token_pair(self, verify):
        response = self.exchange(verify)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        user = User.objects.get(email="zhertva@example.com")
        self.assertTrue(data["created"])
        self.assertEqual(data["user"], {"id": user.pk, "username": user.username})
        self.assertEqual(AccessToken(data["access"])["user_id"], str(user.pk))
        self.assertEqual(RefreshToken(data["refresh"])["user_id"], str(user.pk))
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())

    def test_token_is_checked_against_our_client_ids(self, verify):
        self.exchange(verify)
        args, kwargs = verify.call_args
        self.assertEqual(args[0], "google-id-token")
        self.assertEqual(kwargs["audience"], ["web-client-id", "ios-client-id"])

    def test_existing_verified_user_logs_into_own_account(self, verify):
        user = User.objects.create_user(
            username="zhertva", email="zhertva@example.com", password="parol-parol-42",
            email_verified=True,
        )
        data = self.exchange(verify).json()
        self.assertFalse(data["created"])
        self.assertEqual(data["user"]["id"], user.pk)
        user.refresh_from_db()
        self.assertTrue(user.check_password("parol-parol-42"))

    def test_squatted_account_loses_password_and_tokens(self, verify):
        # Хтось зареєструвався з чужою адресою і тримає JWT на своєму пристрої.
        squatter = User.objects.create_user(
            username="atakuvalnyk", email="zhertva@example.com", password="parol-ataky-42"
        )
        attacker = APIClient()
        attacker.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(squatter)}")

        self.assertEqual(self.exchange(verify).status_code, 200)

        squatter.refresh_from_db()
        self.assertTrue(squatter.email_verified)
        self.assertFalse(squatter.has_usable_password())
        self.assertEqual(attacker.get(reverse("api:sync")).status_code, 401)

    def test_unverified_google_email_rejected(self, verify):
        for value in (False, "true", None):
            with self.subTest(email_verified=value):
                response = self.exchange(verify, {"email_verified": value})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("access", response.json())
        self.assertFalse(User.objects.filter(email="zhertva@example.com").exists())

    def test_invalid_token_is_401(self, verify):
        verify.side_effect = ValueError("Token has wrong audience")
        response = APIClient().post(reverse("api:auth_google"), {"id_token": "x"}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_google_unreachable_is_503(self, verify):
        verify.side_effect = TransportError("certs")
        response = APIClient().post(reverse("api:auth_google"), {"id_token": "x"}, format="json")
        self.assertEqual(response.status_code, 503)

    def test_missing_id_token_is_400(self, verify):
        for body in ({}, {"id_token": ""}, {"id_token": 42}):
            with self.subTest(body=body):
                response = APIClient().post(reverse("api:auth_google"), body, format="json")
                self.assertEqual(response.status_code, 400)
        verify.assert_not_called()

    @override_settings(GOOGLE_TOKEN_AUDIENCES=[])
    def test_without_client_ids_endpoint_refuses(self, verify):
        # Порожній aud = «не перевіряти отримувача» — токен чужого застосунку пройшов би.
        response = self.exchange(verify)
        self.assertEqual(response.status_code, 503)
        verify.assert_not_called()
        self.assertFalse(User.objects.filter(email="zhertva@example.com").exists())

    def test_inactive_user_rejected(self, verify):
        User.objects.create_user(
            username="zhertva", email="zhertva@example.com", email_verified=True, is_active=False
        )
        response = self.exchange(verify)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("access", response.json())

    def test_throttled(self, verify):
        statuses = [self.exchange(verify).status_code for _ in range(11)]
        self.assertEqual(statuses[-1], 429)


class LogoutTests(ApiTestCase):
    def refresh(self, token):
        return APIClient().post(reverse("api:auth_token_refresh"), {"refresh": token})

    def test_logout_revokes_this_device_refresh(self):
        here = str(RefreshToken.for_user(self.user))
        other = str(RefreshToken.for_user(self.user))

        response = APIClient().post(reverse("api:auth_logout"), {"refresh": here})
        self.assertEqual(response.status_code, 200)

        self.assertEqual(self.refresh(here).status_code, 401)
        self.assertEqual(self.refresh(other).status_code, 200)

    def test_logout_works_without_access_token(self):
        # Access міг прострочитись — вихід тримається на самому refresh.
        refresh = str(RefreshToken.for_user(self.user))
        response = APIClient().post(reverse("api:auth_logout"), {"refresh": refresh})
        self.assertEqual(response.status_code, 200)

    def test_logout_with_invalid_refresh_is_401(self):
        response = APIClient().post(reverse("api:auth_logout"), {"refresh": "ne-token"})
        self.assertEqual(response.status_code, 401)

    def test_rotated_refresh_is_blacklisted(self):
        # Без BLACKLIST_AFTER_ROTATION старий refresh жив би поруч із новим.
        old = str(RefreshToken.for_user(self.user))
        new = self.refresh(old).json()["refresh"]
        self.assertEqual(self.refresh(old).status_code, 401)
        self.assertEqual(self.refresh(new).status_code, 200)

    def test_logout_all_revokes_every_device(self):
        phone = str(RefreshToken.for_user(self.user))
        tablet = str(RefreshToken.for_user(self.user))
        foreign = str(RefreshToken.for_user(self.teacher))

        self.assertEqual(self.client.post(reverse("api:auth_logout_all")).status_code, 204)

        self.assertEqual(self.refresh(phone).status_code, 401)
        self.assertEqual(self.refresh(tablet).status_code, 401)
        self.assertEqual(self.refresh(foreign).status_code, 200)

    def test_logout_all_twice_is_fine(self):
        RefreshToken.for_user(self.user)
        self.assertEqual(self.client.post(reverse("api:auth_logout_all")).status_code, 204)
        self.assertEqual(self.client.post(reverse("api:auth_logout_all")).status_code, 204)

    def test_logout_all_requires_access_token(self):
        self.assertEqual(APIClient().post(reverse("api:auth_logout_all")).status_code, 401)


class MeTests(ApiTestCase):
    def test_returns_profile(self):
        data = self.client.get(reverse("api:me")).json()
        self.assertEqual(data["id"], self.user.pk)
        self.assertEqual(data["username"], "uchen")
        self.assertEqual(data["email"], "uchen@example.com")
        self.assertFalse(data["email_verified"])
        self.assertTrue(data["has_password"])
        self.assertIn("date_joined", data)

    def test_google_user_has_no_password(self):
        self.user.set_unusable_password()
        self.user.save()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}")
        self.assertFalse(client.get(reverse("api:me")).json()["has_password"])

    def test_requires_token(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get(reverse("api:me")).status_code, 401)
        self.assertEqual(anonymous.delete(reverse("api:me")).status_code, 401)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_delete_removes_account_and_files(self):
        book = make_book(self.user, "Моя")
        path = book.file.path
        foreign = make_book(self.teacher, "Чужа")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(reverse("api:me"))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Book.objects.filter(pk=book.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.assertTrue(Book.objects.filter(pk=foreign.pk).exists())

    def test_deleted_account_tokens_stop_working(self):
        refresh = str(RefreshToken.for_user(self.user))
        self.client.delete(reverse("api:me"))

        self.assertEqual(self.client.get(reverse("api:sync")).status_code, 401)
        response = APIClient().post(reverse("api:auth_token_refresh"), {"refresh": refresh})
        self.assertEqual(response.status_code, 401)

    def test_refresh_of_untracked_token_after_delete_is_401(self):
        # Refresh, виданий до ввімкнення blacklist, не записаний в OutstandingToken і не
        # відкликається — але й не має давати 500, коли власника вже нема.
        refresh = RefreshToken.for_user(self.user)
        OutstandingToken.objects.filter(jti=refresh["jti"]).delete()
        self.user.delete()

        response = APIClient().post(reverse("api:auth_token_refresh"), {"refresh": str(refresh)})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.json())


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


def add_cover(book):
    book.cover = SimpleUploadedFile("cover.jpg", b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
    book.save()
    return book


class BookCoverTests(ApiTestCase):
    def test_redirects_to_cover_of_visible_book(self):
        book = add_cover(make_book(self.user, "Моя"))
        response = self.client.get(reverse("api:book_cover", args=[book.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], book.cover.url)

    def test_book_without_cover_is_404(self):
        book = make_book(self.user, "Без обкладинки")
        self.assertEqual(self.client.get(reverse("api:book_cover", args=[book.pk])).status_code, 404)

    def test_hidden_book_is_404(self):
        book = add_cover(make_book(self.teacher, "Чужа"))
        self.assertEqual(self.client.get(reverse("api:book_cover", args=[book.pk])).status_code, 404)

    def test_pull_reports_cover_and_file_version(self):
        with_cover = add_cover(make_book(self.user, "З обкладинкою"))
        plain = make_book(self.user, "Без")
        books = {b["id"]: b for b in self.client.get(reverse("api:sync")).json()["books"]}
        self.assertIs(books[with_cover.pk]["has_cover"], True)
        self.assertIs(books[plain.pk]["has_cover"], False)
        self.assertNotEqual(books[with_cover.pk]["file_version"], books[plain.pk]["file_version"])
        # Хеш, а не ключ сховища.
        self.assertNotIn("books/", books[plain.pk]["file_version"])

    def test_file_version_changes_only_with_file(self):
        book = make_book(self.user, "Стара назва")

        def version():
            books = self.client.get(reverse("api:sync")).json()["books"]
            return next(b["file_version"] for b in books if b["id"] == book.pk)

        before = version()
        book.title = "Нова назва"
        book.save()
        self.assertEqual(version(), before)

        book.file = SimpleUploadedFile("b.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
        book.save()
        self.assertNotEqual(version(), before)

    def test_render_covers_backfill_reaches_delta(self):
        with pymupdf.open() as document:
            document.new_page()
            pdf = document.tobytes()
        book = Book.objects.create(
            owner=self.user, title="Стара",
            file=SimpleUploadedFile("b.pdf", pdf, "application/pdf"),
        )
        since = timezone.now()
        call_command("render_covers", stdout=StringIO())
        delta = self.client.get(reverse("api:sync"), {"since": since.isoformat()}).json()["books"]
        self.assertEqual([(b["id"], b["has_cover"]) for b in delta], [(book.pk, True)])


class GroupJoinTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name="9-А", owner=self.teacher)
        self.group.regenerate_invite_token()
        self.book = make_book(self.teacher, "Підручник")
        GroupAccess.objects.create(group=self.group, book=self.book, granted_by=self.teacher)

    def join(self, token, client=None):
        return (client or self.client).post(
            reverse("api:group_join"), {"token": token}, format="json"
        )

    def test_join_by_token_grants_access_in_next_pull(self):
        since = timezone.now()
        response = self.join(self.group.invite_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "group": {
                    "id": self.group.pk, "name": "9-А",
                    "owner": self.teacher.pk, "owner_username": "vchytel",
                },
                "joined": True,
            },
        )
        self.assertTrue(GroupMembership.objects.filter(group=self.group, user=self.user).exists())
        delta = self.client.get(reverse("api:sync"), {"since": since.isoformat()}).json()
        self.assertIn(self.book.pk, [b["id"] for b in delta["books"]])

    def test_repeat_join_is_idempotent(self):
        self.join(self.group.invite_token)
        response = self.join(self.group.invite_token)
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["joined"], False)
        self.assertEqual(GroupMembership.objects.filter(group=self.group, user=self.user).count(), 1)

    def test_join_clears_pending_invitation(self):
        GroupInvitation.objects.create(group=self.group, user=self.user, invited_by=self.teacher)
        self.join(self.group.invite_token)
        self.assertFalse(GroupInvitation.objects.filter(user=self.user).exists())

    def test_unknown_or_missing_token(self):
        self.assertEqual(self.join("ne-isnuye").status_code, 404)
        self.assertEqual(
            self.client.post(reverse("api:group_join"), {}, format="json").status_code, 400
        )

    def test_regenerated_token_stops_working(self):
        old = self.group.invite_token
        self.group.regenerate_invite_token()
        self.assertEqual(self.join(old).status_code, 404)

    def test_owner_cannot_join_own_group(self):
        teacher = APIClient()
        teacher.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.teacher)}")
        response = self.join(self.group.invite_token, client=teacher)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertFalse(GroupMembership.objects.filter(user=self.teacher).exists())

    def test_requires_auth(self):
        self.assertEqual(self.join(self.group.invite_token, client=APIClient()).status_code, 401)


class InvitationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name="9-А", owner=self.teacher)
        self.invitation = GroupInvitation.objects.create(
            group=self.group, user=self.user, invited_by=self.teacher
        )
        other = User.objects.create_user(username="insha", password="parol-parol-42")
        self.foreign = GroupInvitation.objects.create(
            group=self.group, user=other, invited_by=self.teacher
        )

    def test_lists_only_own_invitations(self):
        response = self.client.get(reverse("api:invitations"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([i["id"] for i in data], [self.invitation.pk])
        self.assertEqual(data[0]["group"]["name"], "9-А")
        self.assertEqual(data[0]["invited_by_username"], "vchytel")

    def test_invitation_alone_grants_nothing(self):
        book = make_book(self.teacher, "Підручник")
        GroupAccess.objects.create(group=self.group, book=book, granted_by=self.teacher)
        self.assertNotIn(book.pk, self.client.get(reverse("api:sync")).json()["visible_book_ids"])

    def test_accept_creates_membership_and_removes_invitation(self):
        response = self.client.post(reverse("api:invitation_accept", args=[self.invitation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"]["id"], self.group.pk)
        self.assertIs(response.json()["joined"], True)
        self.assertTrue(GroupMembership.objects.filter(group=self.group, user=self.user).exists())
        self.assertFalse(GroupInvitation.objects.filter(pk=self.invitation.pk).exists())
        # Повтор — запрошення вже нема.
        self.assertEqual(
            self.client.post(reverse("api:invitation_accept", args=[self.invitation.pk])).status_code,
            404,
        )

    def test_decline_removes_invitation_without_membership(self):
        response = self.client.post(reverse("api:invitation_decline", args=[self.invitation.pk]))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(GroupInvitation.objects.filter(pk=self.invitation.pk).exists())
        self.assertFalse(GroupMembership.objects.filter(user=self.user).exists())

    def test_foreign_invitation_is_404(self):
        for name in ("api:invitation_accept", "api:invitation_decline"):
            response = self.client.post(reverse(name, args=[self.foreign.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.assertTrue(GroupInvitation.objects.filter(pk=self.foreign.pk).exists())
        self.assertFalse(GroupMembership.objects.filter(user=self.user).exists())

    def test_get_is_not_allowed_on_actions(self):
        response = self.client.get(reverse("api:invitation_accept", args=[self.invitation.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(GroupInvitation.objects.filter(pk=self.invitation.pk).exists())


def pdf_bytes(pages=1, title=""):
    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page()
        if title:
            document.set_metadata({"title": title})
        return document.tobytes()


def pdf_upload(name="kniga.pdf", pages=1, title=""):
    return SimpleUploadedFile(name, pdf_bytes(pages, title), "application/pdf")


def client_for(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


class BookUploadTests(ApiTestCase):
    def upload(self, **data):
        return self.client.post(reverse("api:books"), data, format="multipart")

    def test_upload_goes_through_inspect_pdf(self):
        folder = Folder.objects.create(owner=self.user, name="Мої")
        response = self.upload(file=pdf_upload(pages=3), title="Алгебра 9", folder=folder.pk)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        book = Book.objects.get(pk=data["id"])
        self.assertEqual(
            (book.owner, book.folder, book.title, book.page_count), (self.user, folder, "Алгебра 9", 3)
        )
        self.assertIs(data["has_cover"], True)
        self.assertEqual(data["page_count"], 3)
        self.assertIn(book.pk, self.client.get(reverse("api:sync")).json()["visible_book_ids"])

    def test_title_falls_back_to_pdf_metadata_then_file_name(self):
        from_meta = self.upload(file=pdf_upload(title="Геометрія"), title="  ")
        self.assertEqual(from_meta.json()["title"], "Геометрія")
        from_name = self.upload(file=pdf_upload(name="fizyka-9.pdf"))
        self.assertEqual(from_name.json()["title"], "fizyka-9")

    def test_invalid_files_rejected(self):
        with pymupdf.open() as document:
            document.new_page()
            locked = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw")
        for name, payload in [
            ("not-pdf", b"<html>not a pdf</html>"),
            ("locked", locked),
            ("broken", b"%PDF-1.4\nbroken"),
        ]:
            with self.subTest(name):
                response = self.upload(file=SimpleUploadedFile("x.pdf", payload, "application/pdf"))
                self.assertEqual(response.status_code, 400)
                self.assertIn("file", response.json())
        self.assertFalse(Book.objects.exists())

    def test_foreign_folder_rejected(self):
        foreign = Folder.objects.create(owner=self.teacher, name="Чужа")
        response = self.upload(file=pdf_upload(), folder=foreign.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("folder", response.json())
        self.assertFalse(Book.objects.exists())

    @override_settings(MAX_UPLOAD_SIZE=100)
    def test_size_limit(self):
        # Файл більший за ліміт, але запит у межах запасу — відсіює inspect_pdf().
        self.assertIn("file", self.upload(file=pdf_upload()).json())
        # Запит більший за ліміт із запасом — 413 ще до розбору тіла.
        huge = SimpleUploadedFile("x.pdf", b"%PDF-" + b"0" * (2 * 1024 * 1024), "application/pdf")
        response = self.upload(file=huge)
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.json())

    def test_json_body_rejected(self):
        response = self.client.post(reverse("api:books"), {"title": "x"}, format="json")
        self.assertEqual(response.status_code, 415)

    @patch("api.views.BookUploadThrottle.rate", "2/hour", create=True)
    def test_upload_is_throttled_per_user(self):
        statuses = [self.upload(file=pdf_upload()).status_code for _ in range(3)]
        self.assertEqual(statuses, [201, 201, 429])
        self.assertEqual(
            client_for(self.teacher).post(
                reverse("api:books"), {"file": pdf_upload()}, format="multipart"
            ).status_code,
            201,
        )


class BookEditTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Стара назва")
        self.folder = Folder.objects.create(owner=self.user, name="Мої")

    def patch(self, book, data):
        return self.client.patch(reverse("api:book_detail", args=[book.pk]), data, format="json")

    def test_rename_and_move_reach_delta(self):
        since = timezone.now()
        response = self.patch(self.book, {"title": " Нова назва ", "folder": self.folder.pk})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["title"], response.json()["folder"]), ("Нова назва", self.folder.pk))
        delta = self.client.get(reverse("api:sync"), {"since": since.isoformat()}).json()
        self.assertEqual([(b["id"], b["folder"]) for b in delta["books"]], [(self.book.pk, self.folder.pk)])

        response = self.patch(self.book, {"folder": None})
        self.assertIsNone(response.json()["folder"])
        self.assertEqual(response.json()["title"], "Нова назва")

    def test_invalid_changes_rejected(self):
        foreign = Folder.objects.create(owner=self.teacher, name="Чужа")
        self.assertIn("folder", self.patch(self.book, {"folder": foreign.pk}).json())
        self.assertIn("title", self.patch(self.book, {"title": "   "}).json())
        self.book.refresh_from_db()
        self.assertEqual((self.book.title, self.book.folder_id), ("Стара назва", None))

    def test_delete(self):
        response = self.client.delete(reverse("api:book_detail", args=[self.book.pk]))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Book.objects.filter(pk=self.book.pk).exists())
        self.assertNotIn(self.book.pk, self.client.get(reverse("api:sync")).json()["visible_book_ids"])

    def test_visible_foreign_book_is_read_only(self):
        # Доступ через групу дає перегляд, а не зміну: queryset — owner=user.
        shared = make_book(self.teacher, "Спільна")
        group = Group.objects.create(name="9-А", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        GroupAccess.objects.create(group=group, book=shared, granted_by=self.teacher)
        self.assertEqual(self.patch(shared, {"title": "Моя"}).status_code, 404)
        self.assertEqual(
            self.client.delete(reverse("api:book_detail", args=[shared.pk])).status_code, 404
        )
        shared.refresh_from_db()
        self.assertEqual(shared.title, "Спільна")


class FolderApiTests(ApiTestCase):
    def create(self, data, client=None):
        return (client or self.client).post(reverse("api:folders"), data, format="json")

    def patch(self, folder, data):
        return self.client.patch(reverse("api:folder_detail", args=[folder.pk]), data, format="json")

    def test_create_nested(self):
        root = self.create({"name": "Математика"})
        self.assertEqual(root.status_code, 201, root.content)
        child = self.create({"name": "Алгебра", "parent": root.json()["id"]})
        self.assertEqual(child.status_code, 201)
        self.assertEqual(child.json()["parent"], root.json()["id"])
        self.assertEqual(Folder.objects.get(pk=child.json()["id"]).owner, self.user)

    def test_foreign_parent_and_blank_name_rejected(self):
        foreign = Folder.objects.create(owner=self.teacher, name="Чужа")
        self.assertIn("parent", self.create({"name": "x", "parent": foreign.pk}).json())
        self.assertIn("name", self.create({"name": "  "}).json())
        self.assertFalse(Folder.objects.filter(owner=self.user).exists())

    def test_cannot_move_into_itself_or_subfolder(self):
        root = Folder.objects.create(owner=self.user, name="Корінь")
        child = Folder.objects.create(owner=self.user, name="Дитина", parent=root)
        grandchild = Folder.objects.create(owner=self.user, name="Онук", parent=child)
        for target in (root, child, grandchild):
            with self.subTest(target=target.name):
                self.assertEqual(self.patch(root, {"parent": target.pk}).status_code, 400)
        root.refresh_from_db()
        self.assertIsNone(root.parent_id)

        response = self.patch(grandchild, {"parent": None, "name": "Тепер корінь"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["parent"], response.json()["name"]), (None, "Тепер корінь"))

    def test_catalog_flag_only_for_staff(self):
        self.assertIn("is_catalog", self.create({"name": "Публічна", "is_catalog": True}).json())
        self.assertEqual(self.create({"name": "Звичайна", "is_catalog": False}).status_code, 201)
        self.teacher.is_staff = True
        self.teacher.save()
        response = self.create({"name": "Каталог", "is_catalog": True}, client=client_for(self.teacher))
        self.assertEqual(response.status_code, 201)
        self.assertIs(response.json()["is_catalog"], True)

    def test_delete_moves_books_to_root_and_reaches_delta(self):
        root = Folder.objects.create(owner=self.user, name="Корінь")
        child = Folder.objects.create(owner=self.user, name="Дитина", parent=root)
        book = make_book(self.user, "Вкладена", folder=child)
        since = timezone.now()
        response = self.client.delete(reverse("api:folder_detail", args=[root.pk]))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Folder.objects.filter(pk__in=[root.pk, child.pk]).exists())
        delta = self.client.get(reverse("api:sync"), {"since": since.isoformat()}).json()
        self.assertEqual([(b["id"], b["folder"]) for b in delta["books"]], [(book.pk, None)])
        self.assertEqual(delta["visible_folder_ids"], [])

    def test_foreign_folder_is_404(self):
        foreign = Folder.objects.create(owner=self.teacher, name="Чужа")
        self.assertEqual(self.patch(foreign, {"name": "Моя"}).status_code, 404)
        self.assertEqual(
            self.client.delete(reverse("api:folder_detail", args=[foreign.pk])).status_code, 404
        )
        self.assertTrue(Folder.objects.filter(pk=foreign.pk, name="Чужа").exists())


class LayerPublicationApiTests(ApiTestCase):
    """Автор шару — self.user як організатор групи; учень — окремий користувач."""

    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Підручник")
        self.group = Group.objects.create(name="9-А", owner=self.user)
        GroupAccess.objects.create(group=self.group, book=self.book, granted_by=self.user)
        self.student = User.objects.create_user(username="student", password="parol-parol-42")
        GroupMembership.objects.create(group=self.group, user=self.student)
        self.url = reverse("api:layer_publications", args=[self.book.pk])

    def put(self, group_ids):
        return self.client.put(self.url, {"group_ids": group_ids}, format="json")

    def test_get_without_layer(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"], [])
        self.assertEqual([g["id"] for g in response.json()["publishable_groups"]], [self.group.pk])

    def test_publish_reaches_student_pull_and_unpublish(self):
        response = self.put([self.group.pk])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([g["id"] for g in response.json()["groups"]], [self.group.pk])
        layer = Layer.objects.get(book=self.book, owner=self.user)
        student = client_for(self.student)
        self.assertEqual(
            [l["id"] for l in student.get(reverse("api:sync")).json()["shared_layers"]], [layer.pk]
        )

        response = self.put([])
        self.assertEqual(response.json()["groups"], [])
        self.assertFalse(LayerPublication.objects.exists())
        self.assertEqual(student.get(reverse("api:sync")).json()["shared_layers"], [])

    def test_only_publishable_groups(self):
        # Група без доступу до книги і чужа група — відмова, набір не змінюється.
        no_access = Group.objects.create(name="10-Б", owner=self.user)
        foreign = Group.objects.create(name="Чужа", owner=self.teacher)
        for group in (no_access, foreign):
            with self.subTest(group=group.name):
                response = self.put([self.group.pk, group.pk])
                self.assertEqual(response.status_code, 400)
                self.assertIn("group_ids", response.json())
        self.assertFalse(LayerPublication.objects.exists())

    def test_stale_publication_is_dropped_not_rejected(self):
        # Група втратила доступ до книги, публікація лишилась: GET показує її в groups, а
        # PUT із тим самим набором знімає її мовчки, як форма на сайті, а не дає 400.
        lost = Group.objects.create(name="10-Б", owner=self.user)
        layer = Layer.objects.create(book=self.book, owner=self.user)
        LayerPublication.objects.create(layer=layer, group=lost)
        current = self.client.get(self.url).json()
        self.assertEqual([g["id"] for g in current["groups"]], [lost.pk])
        response = self.put([g["id"] for g in current["groups"]] + [self.group.pk])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([g["id"] for g in response.json()["groups"]], [self.group.pk])

    def test_empty_set_does_not_create_layer(self):
        self.assertEqual(self.put([]).status_code, 200)
        self.assertFalse(Layer.objects.exists())

    def test_invisible_book_is_404(self):
        foreign = make_book(self.teacher, "Чужа")
        url = reverse("api:layer_publications", args=[foreign.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.put(url, {"group_ids": []}, format="json").status_code, 404)


class ShareLinkApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Підручник")
        self.topic = create_note_group(self.book, self.user, "Терміни")

    def create(self, data):
        return self.client.post(reverse("api:share_links"), data, format="json")

    def test_create_and_list_own_links(self):
        response = self.create({"book": self.book.pk})
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        link = ShareLink.objects.get(pk=data["id"])
        self.assertEqual(data["url"], f"http://testserver/s/{link.token}/")
        self.assertEqual((link.created_by, data["annotation_scope"]), (self.user, "all"))
        self.assertIs(data["is_expired"], False)

        ShareLink.objects.create(book=make_book(self.teacher, "Чужа"), token="chuzhyi", created_by=self.teacher)
        listed = self.client.get(reverse("api:share_links")).json()
        self.assertEqual([l["id"] for l in listed], [link.pk])

    def test_link_opens_on_web(self):
        # Universal link: застосунок віддає url браузеру, доступ — у сесії сайту.
        url = self.create({"book": self.book.pk}).json()["url"]
        response = self.client_class().get(url.removeprefix("http://testserver"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Підручник")

    def test_selected_note_groups(self):
        response = self.create(
            {"book": self.book.pk, "include_annotations": True, "note_groups": [self.topic.pk]}
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["annotation_scope"], "selected")
        self.assertEqual(response.json()["note_groups"], [self.topic.pk])

    def test_invalid_links_rejected(self):
        folder = Folder.objects.create(owner=self.user, name="Мої")
        other_book = make_book(self.user, "Інша")
        foreign_book = make_book(self.teacher, "Чужа")
        foreign_topic = create_note_group(foreign_book, self.teacher, "Чужа тема")
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        cases = {
            "neither": {},
            "both": {"book": self.book.pk, "folder": folder.pk},
            "foreign book": {"book": foreign_book.pk},
            "past expiry": {"book": self.book.pk, "expires_at": past},
            "topics without annotations": {"book": self.book.pk, "note_groups": [self.topic.pk]},
            "topics on folder": {
                "folder": folder.pk, "include_annotations": True, "note_groups": [self.topic.pk],
            },
            "topic of other book": {
                "book": other_book.pk, "include_annotations": True, "note_groups": [self.topic.pk],
            },
            "foreign topic": {
                "book": self.book.pk, "include_annotations": True, "note_groups": [foreign_topic.pk],
            },
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertEqual(self.create(data).status_code, 400)
        self.assertFalse(ShareLink.objects.filter(created_by=self.user).exists())

    def test_delete_own_only(self):
        own = ShareLink.objects.create(book=self.book, token="svii", created_by=self.user)
        foreign = ShareLink.objects.create(
            book=make_book(self.teacher, "Чужа"), token="chuzhyi", created_by=self.teacher
        )
        self.assertEqual(
            self.client.delete(reverse("api:share_link_detail", args=[foreign.pk])).status_code, 404
        )
        self.assertEqual(
            self.client.delete(reverse("api:share_link_detail", args=[own.pk])).status_code, 204
        )
        self.assertEqual(list(ShareLink.objects.values_list("pk", flat=True)), [foreign.pk])

    def test_links_still_grant_nothing_in_api(self):
        # Посилання не активується в JWT-клієнті: сесії нема.
        foreign = make_book(self.teacher, "Чужа")
        ShareLink.objects.create(book=foreign, token="t", created_by=self.teacher)
        self.assertNotIn(foreign.pk, self.client.get(reverse("api:sync")).json()["visible_book_ids"])


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
                        "client_id": A1,
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
        self.assertEqual(created["client_id"], A1)
        self.assertEqual(str(annotation.client_id), A1)
        self.assertEqual(annotation.layer.owner, self.user)
        self.assertEqual(annotation.color, "#A5D6A7")
        self.assertEqual(data["errors"], [])

    def new_note(self, client_id, book=None, **fields):
        return {"client_id": client_id, "book": (book or self.book).pk, "page_number": 1,
                "coords": RECT, "color": "#FFEB3B", **fields}

    def test_repeated_create_is_idempotent(self):
        # Коміт пройшов, відповідь загубилась — клієнт повторює той самий пуш.
        first = self.push({"create": [self.new_note(A1, note_text="раз")]})
        retry = self.push({"create": [self.new_note(A1, note_text="раз")]})
        self.assertEqual(retry["errors"], [])
        self.assertEqual(retry["created"][0]["id"], first["created"][0]["id"])
        self.assertEqual(Annotation.objects.count(), 1)

    def test_repeated_create_returns_original_unchanged(self):
        # Повтор — це той самий запит, а не правка: змінений вміст не застосовується.
        first = self.push({"create": [self.new_note(A1, note_text="перша версія")]})
        retry = self.push({"create": [self.new_note(A1, note_text="інший текст")]})
        self.assertEqual(retry["created"][0]["id"], first["created"][0]["id"])
        self.assertEqual(retry["created"][0]["note_text"], "перша версія")

    def test_client_id_is_echoed_as_sent(self):
        data = self.push({"create": [self.new_note(A1.upper())]})
        self.assertEqual(data["created"][0]["client_id"], A1.upper())
        self.assertEqual(str(Annotation.objects.get().client_id), A1)

    def test_same_client_id_in_other_book_is_separate_note(self):
        other = make_book(self.user, "Друга", page_count=5)
        data = self.push({"create": [self.new_note(A1), self.new_note(A1, book=other)]})
        self.assertEqual(data["errors"], [])
        self.assertEqual(len({c["id"] for c in data["created"]}), 2)

    def test_create_without_uuid_client_id_is_error(self):
        note = self.new_note(A1)
        del note["client_id"]
        data = self.push(
            {"create": [note, self.new_note("c-1"), self.new_note(42), self.new_note(A2)]}
        )
        self.assertEqual([c["client_id"] for c in data["created"]], [A2])
        self.assertEqual([e["client_id"] for e in data["errors"]], [None, "c-1", 42])
        self.assertTrue(all("UUID" in e["error"] for e in data["errors"]))
        self.assertEqual(Annotation.objects.count(), 1)

    def test_pull_exposes_own_client_id(self):
        self.push({"create": [self.new_note(A1)]})
        response = self.client.get(reverse("api:sync"))
        self.assertEqual(response.json()["annotations"][0]["client_id"], A1)

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
                    self.new_note(A1, book=self.foreign_book),  # чужа книга
                    self.new_note(A2, page_number=99),  # нема такої сторінки
                    self.new_note(A3),
                ],
                "update": [{"id": foreign.pk, "note_text": "чуже"}],
            }
        )
        self.assertEqual([c["client_id"] for c in data["created"]], [A3])
        self.assertEqual(
            {str(e.get("client_id") or e.get("id")) for e in data["errors"]},
            {A1, A2, str(foreign.pk)},
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
        self.assertNotIn("client_id", notes[0])

    def test_push_creates_group_and_annotation_into_it(self):
        data = self.push(
            {
                "note_groups": {"create": [{"client_id": "t1", "book": self.book.pk, "name": "Офлайн"}]},
                "annotations": {
                    "create": [
                        {"client_id": A1, "book": self.book.pk, "page_number": 1,
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
                        {"client_id": A1, "book": self.book.pk, "page_number": 1,
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
        return {"client_id": A1, "book": self.book.pk, "page_number": 1,
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
                        {"client_id": A1, "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "t1"},
                        {"client_id": A2, "book": self.book.pk, "page_number": 1,
                         "coords": RECT, "color": "#FFEB3B", "group_client_id": "nema"},
                    ]
                },
            }
        )
        self.assertEqual([e["client_id"] for e in data["errors"]], [A1, A2])
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


class FavoriteSyncTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.user, "Моя")
        self.foreign_book = make_book(self.teacher, "Чужа")

    def push(self, favorites):
        response = self.client.post(reverse("api:sync"), {"favorites": favorites}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def pull_ids(self, since=None):
        params = {"since": since.isoformat()} if since else {}
        response = self.client.get(reverse("api:sync"), params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["favorite_book_ids"]

    def test_add_then_pull_full_list_even_in_delta(self):
        since = timezone.now()
        data = self.push({"add": [self.book.pk]})
        self.assertEqual(data["favorites"], {"removed": [], "added": [self.book.pk]})
        self.assertEqual(self.pull_ids(), [self.book.pk])
        self.assertEqual(self.pull_ids(timezone.now()), [self.book.pk])
        self.assertEqual(self.pull_ids(since), [self.book.pk])

    def test_add_is_idempotent_and_remove_works(self):
        self.push({"add": [self.book.pk]})
        self.push({"add": [self.book.pk]})
        self.assertEqual(self.pull_ids(), [self.book.pk])
        self.push({"remove": [self.book.pk]})
        self.assertEqual(self.pull_ids(), [])

    def test_same_id_in_remove_and_add_is_rejected(self):
        self.push({"add": [self.book.pk]})
        data = self.push({"remove": [self.book.pk], "add": [self.book.pk]})
        self.assertEqual(data["favorites"], {"removed": [], "added": []})
        self.assertEqual(
            [e["op"] for e in data["errors"]], ["favorite_remove", "favorite_add"]
        )
        # Нічого не застосовано: книга лишилась в обраному, як і була.
        self.assertEqual(self.pull_ids(), [self.book.pk])

    def test_foreign_book_is_per_item_error(self):
        data = self.push({"add": [self.foreign_book.pk, self.book.pk, "x"]})
        self.assertEqual(data["favorites"]["added"], [self.book.pk])
        self.assertEqual([e["op"] for e in data["errors"]], ["favorite_add", "favorite_add"])
        self.assertEqual(self.pull_ids(), [self.book.pk])

    def test_revoked_book_hidden_from_pull_but_removable(self):
        group = Group.objects.create(name="11-Г", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        access = GroupAccess.objects.create(group=group, book=self.foreign_book, granted_by=self.teacher)
        self.push({"add": [self.foreign_book.pk]})
        self.assertEqual(self.pull_ids(), [self.foreign_book.pk])

        access.delete()
        self.assertEqual(self.pull_ids(), [])
        data = self.push({"remove": [self.foreign_book.pk]})
        self.assertEqual(data["errors"], [])

    def test_malformed_section_is_400(self):
        response = self.client.post(reverse("api:sync"), {"favorites": []}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse("api:sync"), {"favorites": {"add": 5}}, format="json")
        self.assertEqual(response.status_code, 400)


def _strict(node):
    """OpenAPI 3.0 → JSON Schema для валідації відповідей.

    nullable стає anyOf з null; в об'єктах з properties зайві ключі заборонені — інакше
    поле, яке код віддає, а схема не описує, проходило б мовчки.
    """
    if isinstance(node, list):
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: _strict(value) for key, value in node.items()}
    if "properties" in node and "additionalProperties" not in node:
        node["additionalProperties"] = False
    if node.pop("nullable", False):
        return {"anyOf": [node, {"type": "null"}]}
    return node


class OpenApiSchemaTests(ApiTestCase):
    """Схема /api/v1/ — контракт мобільного клієнта: генерується без попереджень,
    а справжні відповіді їй відповідають."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        out = Path(tempfile.mkdtemp(prefix="pdfplus-openapi-")) / "openapi.yaml"
        cls.addClassCleanup(shutil.rmtree, out.parent, ignore_errors=True)
        # --fail-on-warn: в'юха без опису відповіді чи поле невідомого типу — падіння, а не «any».
        call_command("spectacular", "--file", str(out), "--validate", "--fail-on-warn")
        cls.schema = yaml.safe_load(out.read_text(encoding="utf-8"))

    def assert_matches(self, response, path, method, status_code=200):
        self.assertEqual(response.status_code, status_code, response.content)
        schema = self.schema["paths"][path][method]["responses"][str(status_code)]
        schema = schema["content"]["application/json"]["schema"]
        root = {**_strict(schema), "components": _strict(self.schema["components"])}
        validator = Draft202012Validator(root, format_checker=Draft202012Validator.FORMAT_CHECKER)
        errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(response.json())]
        self.assertEqual(errors, [])

    def test_only_v1_endpoints(self):
        # API читалки (/api/books/…) сесійне й не версіонується — не контракт для клієнтів.
        self.assertEqual(
            set(self.schema["paths"]),
            {
                "/api/v1/schema/",
                "/api/v1/auth/token/",
                "/api/v1/auth/token/refresh/",
                "/api/v1/auth/google/",
                "/api/v1/auth/logout/",
                "/api/v1/auth/logout/all/",
                "/api/v1/me/",
                "/api/v1/catalog/books/",
                "/api/v1/books/",
                "/api/v1/books/{id}/",
                "/api/v1/books/{id}/file/",
                "/api/v1/books/{id}/cover/",
                "/api/v1/folders/",
                "/api/v1/folders/{id}/",
                "/api/v1/layers/{book_id}/publications/",
                "/api/v1/share-links/",
                "/api/v1/share-links/{id}/",
                "/api/v1/groups/join/",
                "/api/v1/invitations/",
                "/api/v1/invitations/{id}/accept/",
                "/api/v1/invitations/{id}/decline/",
                "/api/v1/sync/",
            },
        )

    def test_operation_ids_are_explicit(self):
        # operationId — імена методів у згенерованому клієнті: перейменування ламає його збірку.
        ids = sorted(
            op["operationId"]
            for path, item in self.schema["paths"].items()
            if path != "/api/v1/schema/"
            for op in item.values()
        )
        self.assertEqual(
            ids,
            sorted([
                "obtainToken", "refreshToken", "googleSignIn", "logout", "logoutAll",
                "getMe", "deleteMe", "listCatalogBooks", "getBookFile", "getBookCover",
                "joinGroup", "listInvitations", "acceptInvitation", "declineInvitation",
                "pullSync", "pushSync",
                "uploadBook", "updateBook", "deleteBook",
                "createFolder", "updateFolder", "deleteFolder",
                "getLayerPublications", "setLayerPublications",
                "listShareLinks", "createShareLink", "deleteShareLink",
            ]),
        )

    def test_book_and_folder_responses_match(self):
        upload = self.client.post(reverse("api:books"), {"file": pdf_upload()}, format="multipart")
        self.assert_matches(upload, "/api/v1/books/", "post", 201)
        self.assertEqual(
            list(self.schema["paths"]["/api/v1/books/"]["post"]["requestBody"]["content"]),
            ["multipart/form-data"],
        )
        bad = SimpleUploadedFile("x.pdf", b"not a pdf", "application/pdf")
        self.assert_matches(
            self.client.post(reverse("api:books"), {"file": bad}, format="multipart"),
            "/api/v1/books/", "post", 400,
        )
        with override_settings(MAX_UPLOAD_SIZE=1):
            huge = SimpleUploadedFile("x.pdf", b"%PDF-" + b"0" * (2 * 1024 * 1024), "application/pdf")
            self.assert_matches(
                self.client.post(reverse("api:books"), {"file": huge}, format="multipart"),
                "/api/v1/books/", "post", 413,
            )

        folder = self.client.post(reverse("api:folders"), {"name": "Мої"}, format="json")
        self.assert_matches(folder, "/api/v1/folders/", "post", 201)
        folder_url = reverse("api:folder_detail", args=[folder.json()["id"]])
        self.assert_matches(
            self.client.patch(folder_url, {"name": "Нова"}, format="json"),
            "/api/v1/folders/{id}/", "patch",
        )
        self.assert_matches(
            self.client.patch(folder_url, {"parent": folder.json()["id"]}, format="json"),
            "/api/v1/folders/{id}/", "patch", 400,
        )
        self.assert_matches(
            self.client.patch(
                reverse("api:book_detail", args=[upload.json()["id"]]),
                {"folder": folder.json()["id"]}, format="json",
            ),
            "/api/v1/books/{id}/", "patch",
        )

    def test_layer_publications_match(self):
        book = make_book(self.user, "Власна")
        group = Group.objects.create(name="9-А", owner=self.user)
        GroupAccess.objects.create(group=group, book=book, granted_by=self.user)
        url = reverse("api:layer_publications", args=[book.pk])
        path = "/api/v1/layers/{book_id}/publications/"
        self.assert_matches(self.client.get(url), path, "get")
        self.assert_matches(self.client.put(url, {"group_ids": [group.pk]}, format="json"), path, "put")
        self.assert_matches(self.client.put(url, {"group_ids": [999999]}, format="json"), path, "put", 400)

    def test_share_links_match(self):
        book = make_book(self.user, "Власна")
        topic = create_note_group(book, self.user, "Терміни")
        url = reverse("api:share_links")
        created = self.client.post(
            url,
            {"book": book.pk, "include_annotations": True, "note_groups": [topic.pk],
             "expires_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )
        self.assert_matches(created, "/api/v1/share-links/", "post", 201)
        self.assert_matches(self.client.post(url, {}, format="json"), "/api/v1/share-links/", "post", 400)
        self.assert_matches(self.client.get(url), "/api/v1/share-links/", "get")

    def test_schema_and_docs_served_without_auth(self):
        response = APIClient().get("/api/v1/schema/", {"format": "json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["info"]["version"], "1")
        self.assertEqual(APIClient().get("/api/v1/docs/").status_code, 200)
        self.assertEqual(APIClient().get("/api/v1/redoc/").status_code, 200)

    def test_token_responses_match(self):
        client = APIClient()
        response = client.post(
            reverse("api:auth_token"), {"username": "uchen", "password": "parol-parol-42"}
        )
        self.assert_matches(response, "/api/v1/auth/token/", "post")
        refresh = client.post(reverse("api:auth_token_refresh"), {"refresh": response.json()["refresh"]})
        self.assert_matches(refresh, "/api/v1/auth/token/refresh/", "post")
        logout = client.post(reverse("api:auth_logout"), {"refresh": refresh.json()["refresh"]})
        self.assert_matches(logout, "/api/v1/auth/logout/", "post")

    @override_settings(GOOGLE_TOKEN_AUDIENCES=["web-client-id"])
    @patch("api.views.id_token.verify_oauth2_token")
    def test_google_response_matches(self, verify):
        verify.return_value = {**GOOGLE_CLAIMS}
        response = APIClient().post(reverse("api:auth_google"), {"id_token": "t"}, format="json")
        self.assert_matches(response, "/api/v1/auth/google/", "post")
        response = APIClient().post(reverse("api:auth_google"), {}, format="json")
        self.assert_matches(response, "/api/v1/auth/google/", "post", 400)

    def test_me_and_catalog_match(self):
        self.assert_matches(self.client.get(reverse("api:me")), "/api/v1/me/", "get")
        catalog = Folder.objects.create(owner=self.teacher, name="Каталог", is_catalog=True)
        make_book(self.teacher, "Фізика", folder=catalog)
        self.assert_matches(
            self.client.get(reverse("api:catalog_books")), "/api/v1/catalog/books/", "get"
        )

    def test_group_join_and_invitations_match(self):
        group = Group.objects.create(name="9-А", owner=self.teacher)
        group.regenerate_invite_token()
        other = Group.objects.create(name="10-Б", owner=self.teacher)
        invitation = GroupInvitation.objects.create(group=other, user=self.user, invited_by=self.teacher)

        join = reverse("api:group_join")
        self.assert_matches(
            self.client.post(join, {"token": group.invite_token}, format="json"),
            "/api/v1/groups/join/", "post",
        )
        self.assert_matches(
            self.client.post(join, {}, format="json"), "/api/v1/groups/join/", "post", 400
        )
        self.assert_matches(
            self.client.post(join, {"token": "ne-isnuye"}, format="json"),
            "/api/v1/groups/join/", "post", 404,
        )
        self.assert_matches(self.client.get(reverse("api:invitations")), "/api/v1/invitations/", "get")
        self.assert_matches(
            self.client.post(reverse("api:invitation_accept", args=[invitation.pk])),
            "/api/v1/invitations/{id}/accept/", "post",
        )
        self.assert_matches(
            self.client.post(reverse("api:invitation_decline", args=[invitation.pk])),
            "/api/v1/invitations/{id}/decline/", "post", 404,
        )

    def test_pull_matches(self):
        folder = Folder.objects.create(owner=self.user, name="Мої")
        book = add_cover(make_book(self.user, "Власна", folder=folder))
        topic = create_note_group(book, self.user, "Терміни")
        self.annotate(book, note_text="нотатка", group=topic)
        record_progress(self.user, book, 3, timezone.now())
        self.client.post(reverse("api:sync"), {"favorites": {"add": [book.pk]}}, format="json")

        group = Group.objects.create(name="9-А", owner=self.teacher)
        GroupMembership.objects.create(group=group, user=self.user)
        shared = make_book(self.teacher, "Спільна")
        GroupAccess.objects.create(group=group, book=shared, granted_by=self.teacher)
        teacher_note = self.annotate(shared, owner=self.teacher)
        LayerPublication.objects.create(layer=teacher_note.layer, group=group)

        before = timezone.now() - timedelta(minutes=1)
        self.annotate(book).delete()

        full = self.client.get(reverse("api:sync"))
        self.assert_matches(full, "/api/v1/sync/", "get")
        for key in ("books", "annotations", "note_groups", "shared_layers", "shared_annotations",
                    "reading_progress", "favorite_book_ids"):
            self.assertTrue(full.json()[key], key)

        delta = self.client.get(reverse("api:sync"), {"since": before.isoformat()})
        self.assert_matches(delta, "/api/v1/sync/", "get")
        self.assertTrue(delta.json()["deleted_annotation_ids"])

        self.assert_matches(
            self.client.get(reverse("api:sync"), {"since": "vchora"}), "/api/v1/sync/", "get", 400
        )

    def test_push_matches(self):
        book = make_book(self.user, "Власна")
        existing = self.annotate(book)
        doomed = self.annotate(book)
        response = self.client.post(
            reverse("api:sync"),
            {
                "note_groups": {"create": [{"client_id": "t-1", "book": book.pk, "name": "Дати"}]},
                "annotations": {
                    "create": [
                        {"client_id": A1, "book": book.pk, "page_number": 1, "coords": RECT,
                         "color": "#90CAF9", "group_client_id": "t-1"},
                        {"client_id": "ne-uuid", "book": book.pk},
                    ],
                    "update": [{"id": existing.pk, "note_text": "нове"}],
                    "delete": [doomed.pk],
                },
                "reading_progress": [
                    {"book": book.pk, "last_page": 2, "read_at": timezone.now().isoformat()}
                ],
                "favorites": {"add": [book.pk]},
            },
            format="json",
        )
        self.assert_matches(response, "/api/v1/sync/", "post")
        data = response.json()
        self.assertEqual([e["op"] for e in data["errors"]], ["create"])
        self.assertEqual(len(data["created"]), 1)

        self.assertEqual(
            list(self.schema["paths"]["/api/v1/sync/"]["post"]["requestBody"]["content"]),
            ["application/json"],
        )
        self.assertEqual(self.client.post(reverse("api:sync"), {"x": "1"}).status_code, 415)

        too_many = {"favorites": {"add": list(range(1, 502))}}
        self.assert_matches(
            self.client.post(reverse("api:sync"), too_many, format="json"),
            "/api/v1/sync/", "post", 400,
        )
