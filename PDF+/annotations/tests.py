import atexit
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from book_library.access import SESSION_TOKENS_KEY
from book_library.models import Book, Folder
from book_sharing_settings.testing import FILESYSTEM_STORAGES
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db.models import QuerySet
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from sharing.models import Group, GroupAccess, GroupMembership, ShareLink
from users.models import User

from .access import publishable_groups, shared_annotations, shared_layers
from .groups import create_note_group, delete_note_group, group_fields, rename_note_group
from .models import Annotation, Layer, LayerPublication, NoteGroup
from .validators import MAX_NOTE_LENGTH, PayloadError, merge_rects, parse_coords, parse_group_name

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")
# Не tearDownModule: за --parallel на Linux (fork) теку ділять усі воркери, і той, що
# закінчив свій клас, видаляв її з-під інших. За fork atexit спрацьовує лише в батьківському процесі.
atexit.register(shutil.rmtree, TEST_MEDIA_ROOT, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class AnnotationPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uchen", email="uchen@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user,
            title="Історія",
            file=SimpleUploadedFile("h.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )
        self.layer = Layer.objects.create(book=self.book, owner=self.user)
        self.annotation = Annotation.objects.create(
            layer=self.layer,
            page_number=7,
            coords=[{"x0": 10, "y0": 20, "x1": 100, "y1": 40}],
            selected_text="Козацька доба",
            note_text="Питання до заліку",
            color="#FFEB3B",
        )

    def test_list_groups_by_page(self):
        response = self.client.get(reverse("annotations:book_annotations", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сторінка 7")
        self.assertContains(response, "Козацька доба")
        self.assertContains(response, "Питання до заліку")

    def test_summary_counts(self):
        response = self.client.get(reverse("annotations:book_annotations", args=[self.book.pk]))
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["pages_with_notes"], 1)
        self.assertEqual(response.context["with_notes_count"], 1)

    def test_empty_state_when_no_layer(self):
        Annotation.objects.all().delete()
        Layer.objects.all().delete()
        response = self.client.get(reverse("annotations:book_annotations", args=[self.book.pk]))
        self.assertContains(response, "Нотаток немає")

    def test_reader_shows_annotations_panel(self):
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertContains(response, "Козацька доба")
        self.assertContains(response, 'id="annotations-data"')

    def test_go_to_book_link_carries_page_and_annotation(self):
        response = self.client.get(reverse("annotations:book_annotations", args=[self.book.pk]))
        reader_url = reverse("book_library:book_reader", args=[self.book.pk])
        self.assertContains(
            response,
            f"{reader_url}?page=7&amp;annotation={self.annotation.pk}",
        )

    def test_delete_requires_post_and_ownership(self):
        stranger = User.objects.create_user(
            username="inshyi", email="inshyi@example.com", password="parol-parol-42"
        )
        url = reverse("annotations:annotation_delete", args=[self.annotation.pk])

        self.client.get(url)
        self.assertTrue(Annotation.objects.filter(pk=self.annotation.pk).exists())

        self.client.force_login(stranger)
        self.assertEqual(self.client.post(url).status_code, 404)

        self.client.force_login(self.user)
        self.client.post(url)
        self.assertFalse(Annotation.objects.filter(pk=self.annotation.pk).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class AnnotationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avtor-notatok", email="an@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user,
            title="Біологія",
            page_count=10,
            file=SimpleUploadedFile("b.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )
        self.create_url = reverse("annotations:api_book_annotations", args=[self.book.pk])

    def post(self, **overrides):
        payload = {
            "page_number": 3,
            "coords": [{"x0": 72.0, "y0": 100.0, "x1": 300.0, "y1": 118.0}],
            "color": "#FFEB3B",
            "selected_text": "Фотосинтез",
            "note_text": "",
        }
        payload.update(overrides)
        return self.client.post(
            self.create_url, data=payload, content_type="application/json"
        )

    def test_create_makes_layer_and_annotation(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)

        body = response.json()
        self.assertEqual(body["page"], 3)
        self.assertEqual(body["color"], "#FFEB3B")
        self.assertEqual(body["text"], "Фотосинтез")

        layer = Layer.objects.get(book=self.book, owner=self.user)
        self.assertEqual(Annotation.objects.filter(layer=layer).count(), 1)

    def test_second_annotation_reuses_layer(self):
        self.post()
        self.post(page_number=4)
        self.assertEqual(Layer.objects.filter(book=self.book, owner=self.user).count(), 1)
        self.assertEqual(Annotation.objects.count(), 2)

    def test_every_palette_colour_accepted(self):
        for index, (value, _label) in enumerate(Annotation.PALETTE, start=1):
            with self.subTest(color=value):
                response = self.post(color=value, page_number=index)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["color"], value)
        self.assertEqual(Annotation.objects.count(), len(Annotation.PALETTE))

    def test_invalid_payloads_rejected(self):
        cases = {
            "колір не hex": {"color": "red"},
            "колір без решітки": {"color": "FFEB3B"},
            "сторінка поза межами": {"page_number": 99},
            "сторінка нуль": {"page_number": 0},
            "coords не список": {"coords": {"x0": 1}},
            "coords порожні": {"coords": []},
            "coords без ключа": {"coords": [{"x0": 1, "y0": 2, "x1": 3}]},
            "coords зайвий ключ": {"coords": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4, "z": 5}]},
            "coords не число": {"coords": [{"x0": "a", "y0": 2, "x1": 3, "y1": 4}]},
            "вироджений прямокутник": {"coords": [{"x0": 5, "y0": 2, "x1": 5, "y1": 4}]},
            "забагато прямокутників": {
                "coords": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}] * 201
            },
            "задовга нотатка": {"note_text": "я" * (MAX_NOTE_LENGTH + 1)},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                response = self.post(**override)
                self.assertEqual(response.status_code, 400, name)
                self.assertIn("error", response.json())
        self.assertFalse(Annotation.objects.exists())

    def test_broken_json_body_rejected(self):
        response = self.client.post(
            self.create_url, data="{не json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_non_json_body_rejected(self):
        response = self.client.post(self.create_url, data={"page_number": 3})
        self.assertEqual(response.status_code, 415)
        self.assertIn("error", response.json())
        self.assertFalse(Annotation.objects.exists())

    def test_anonymous_cannot_create(self):
        self.client.logout()
        response = self.post()
        # JSON-API не редиректить на сторінку входу: fetch у читалці отримав би HTML.
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())
        self.assertFalse(Annotation.objects.exists())

    def test_cannot_annotate_invisible_book(self):
        stranger = User.objects.create_user(
            username="chuzhak-notatok", email="cn@example.com", password="parol-parol-42"
        )
        self.client.force_login(stranger)
        response = self.post()
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_can_annotate_book_shared_via_group(self):
        from sharing.models import Group, GroupAccess, GroupMembership

        student = User.objects.create_user(
            username="uchen-notatok", email="un@example.com", password="parol-parol-42"
        )
        group = Group.objects.create(owner=self.user, name="Клас")
        GroupMembership.objects.create(group=group, user=student)
        GroupAccess.objects.create(group=group, book=self.book, granted_by=self.user)

        self.client.force_login(student)
        self.assertEqual(self.post().status_code, 201)

        self.assertTrue(Layer.objects.filter(book=self.book, owner=student).exists())
        self.assertFalse(Layer.objects.filter(book=self.book, owner=self.user).exists())

    def test_csrf_is_enforced(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = strict.post(
            self.create_url,
            data={"page_number": 1, "coords": [{"x0": 1, "y0": 2, "x1": 3, "y1": 4}],
                  "color": "#FFEB3B"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["error"])


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class AnnotationApiUpdateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pravnyk", email="pr@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user, title="Хімія", page_count=5,
            file=SimpleUploadedFile("c.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )
        self.layer = Layer.objects.create(book=self.book, owner=self.user)
        self.annotation = Annotation.objects.create(
            layer=self.layer,
            page_number=2,
            coords=[{"x0": 10, "y0": 20, "x1": 100, "y1": 40}],
            color="#FFEB3B",
        )
        self.url = reverse("annotations:api_annotation_detail", args=[self.annotation.pk])

    def patch(self, payload):
        return self.client.patch(self.url, data=payload, content_type="application/json")

    def test_change_colour(self):
        response = self.patch({"color": "#90CAF9"})
        self.assertEqual(response.status_code, 200)
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.color, "#90CAF9")

    def test_add_note(self):
        self.patch({"note_text": "Вивчити до контрольної"})
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.note_text, "Вивчити до контрольної")

    def test_note_length_limit(self):
        self.assertEqual(self.patch({"note_text": "я" * MAX_NOTE_LENGTH}).status_code, 200)
        self.assertEqual(
            self.patch({"note_text": "я" * (MAX_NOTE_LENGTH + 1)}).status_code, 400
        )
        self.annotation.refresh_from_db()
        self.assertEqual(len(self.annotation.note_text), MAX_NOTE_LENGTH)

    def test_immutable_fields_rejected(self):
        for payload in [
            {"page_number": 4},
            {"coords": [{"x0": 0, "y0": 0, "x1": 9, "y1": 9}]},
            {"layer": 7},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.patch(payload).status_code, 400)

        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.page_number, 2)

    def test_empty_patch_rejected(self):
        self.assertEqual(self.patch({}).status_code, 400)

    def test_foreign_annotation_is_invisible(self):
        stranger = User.objects.create_user(
            username="ne-mii", email="nm@example.com", password="parol-parol-42"
        )
        self.client.force_login(stranger)
        self.assertEqual(self.patch({"color": "#A5D6A7"}).status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 404)

    def test_delete(self):
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Annotation.objects.filter(pk=self.annotation.pk).exists())

    def test_list_returns_own_annotations(self):
        url = reverse("annotations:api_book_annotations", args=[self.book.pk])
        body = self.client.get(url).json()
        self.assertEqual(len(body["annotations"]), 1)
        self.assertEqual(body["annotations"][0]["id"], self.annotation.pk)

    def test_wrong_method_rejected(self):
        response = self.client.put(self.url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 405)
        self.assertIn("error", response.json())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class CoordinateRoundTripTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="koordynaty", email="ko@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user, title="Геометрія", page_count=4,
            file=SimpleUploadedFile("g.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )

    def test_fractional_coords_survive_storage(self):
        coords = [
            {"x0": 71.9921875, "y0": 658.1796875, "x1": 192.65103149414062, "y1": 674.1796875},
            {"x0": 71.9921875, "y0": 640.0, "x1": 150.5, "y1": 656.25},
        ]
        response = self.client.post(
            reverse("annotations:api_book_annotations", args=[self.book.pk]),
            data={"page_number": 1, "coords": coords, "color": "#90CAF9"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["coords"], coords)

        stored = Annotation.objects.get().coords
        self.assertEqual(stored, coords)

    def test_multi_rect_selection_kept_in_order(self):
        coords = [{"x0": i * 10.5, "y0": 10.0, "x1": i * 10.5 + 9.0, "y1": 26.0} for i in range(1, 6)]
        self.client.post(
            reverse("annotations:api_book_annotations", args=[self.book.pk]),
            data={"page_number": 2, "coords": coords, "color": "#FFEB3B"},
            content_type="application/json",
        )
        self.assertEqual(Annotation.objects.get().coords, coords)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class ReaderPaletteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="paletra", email="pa@example.com", password="parol-parol-42"
        )
        self.book = Book.objects.create(
            owner=self.user, title="Палітра", page_count=2,
            file=SimpleUploadedFile("p.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )

    def test_palette_rendered_for_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))

        self.assertContains(response, 'id="highlightPalette"')
        self.assertContains(response, 'id="annotationPopover"')
        for value, _label in Annotation.PALETTE:
            with self.subTest(color=value):
                self.assertContains(response, f'data-color="{value}"')

    def test_api_endpoints_exposed_to_reader(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))

        self.assertContains(response, "data-csrf-token")
        self.assertContains(
            response,
            reverse("annotations:api_book_annotations", args=[self.book.pk]),
        )
        self.assertContains(response, 'data-can-annotate="1"')
        self.assertContains(response, f'maxlength="{MAX_NOTE_LENGTH}"')

    def test_filter_panel_and_palette_data_rendered(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))

        self.assertContains(response, 'id="annotationFilter"')
        self.assertContains(response, "data-filter-chips")
        self.assertContains(response, "data-annotation-prev")
        self.assertContains(response, "data-annotation-next")

        self.assertContains(response, 'id="palette-data"')
        for _value, label in Annotation.PALETTE:
            with self.subTest(label=label):
                self.assertContains(response, label)

    def test_anonymous_reader_has_no_palette(self):
        from sharing.models import ShareLink

        link = ShareLink.objects.create(book=self.book, token="anon-palette", created_by=self.user)
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="highlightPalette"')
        self.assertNotContains(response, "data-can-annotate")


class RectMergeTests(SimpleTestCase):

    def test_overlapping_rects_on_one_line_merge(self):
        merged = parse_coords([
            {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0},
            {"x0": 170.0, "y0": 100.0, "x1": 330.0, "y1": 116.0},
        ])
        self.assertEqual(
            merged,
            [{"x0": 20.0, "y0": 100.0, "x1": 330.0, "y1": 116.0}],
        )

    def test_touching_rects_merge(self):
        merged = parse_coords([
            {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0},
            {"x0": 180.0, "y0": 100.0, "x1": 240.0, "y1": 116.0},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["x1"], 240.0)

    def test_word_gap_is_preserved(self):
        rects = [
            {"x0": 10.5, "y0": 10.0, "x1": 19.5, "y1": 26.0},
            {"x0": 21.0, "y0": 10.0, "x1": 30.0, "y1": 26.0},
        ]
        self.assertEqual(parse_coords(rects), rects)

    def test_different_lines_do_not_merge(self):
        rects = [
            {"x0": 20.0, "y0": 120.0, "x1": 200.0, "y1": 136.0},
            {"x0": 20.0, "y0": 100.0, "x1": 200.0, "y1": 116.0},
        ]
        self.assertEqual(parse_coords(rects), rects)

    def test_subpixel_jitter_counts_as_one_line(self):
        merged = parse_coords([
            {"x0": 71.99, "y0": 658.17, "x1": 192.65, "y1": 674.17},
            {"x0": 190.12, "y0": 658.21, "x1": 240.00, "y1": 674.19},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["x0"], 71.99)
        self.assertEqual(merged[0]["x1"], 240.00)

    def test_duplicate_rects_collapse(self):
        rect = {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0}
        self.assertEqual(parse_coords([rect, dict(rect), dict(rect)]), [rect])

    def test_output_sorted_top_down_then_left_to_right(self):
        merged = parse_coords([
            {"x0": 50.0, "y0": 100.0, "x1": 90.0, "y1": 116.0},
            {"x0": 10.0, "y0": 200.0, "x1": 40.0, "y1": 216.0},
            {"x0": 10.0, "y0": 100.0, "x1": 40.0, "y1": 116.0},
        ])
        self.assertEqual(
            [(r["y0"], r["x0"]) for r in merged],
            [(200.0, 10.0), (100.0, 10.0), (100.0, 50.0)],
        )

    def test_superscript_does_not_swallow_the_line(self):
        body = {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0}
        superscript = {"x0": 178.0, "y0": 112.0, "x1": 184.0, "y1": 120.0}
        merged = parse_coords([body, superscript])
        self.assertEqual(len(merged), 2)
        self.assertIn(body, merged)
        self.assertIn(superscript, merged)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class RectMergeStorageTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="zlyttia", email="zl@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = Book.objects.create(
            owner=self.user, title="Перекриття", page_count=4,
            file=SimpleUploadedFile("p.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
        )

    def test_api_stores_merged_coords(self):
        response = self.client.post(
            reverse("annotations:api_book_annotations", args=[self.book.pk]),
            data={
                "page_number": 1,
                "color": "#FFEB3B",
                "coords": [
                    {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0},
                    {"x0": 170.0, "y0": 100.0, "x1": 330.0, "y1": 116.0},
                ],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            Annotation.objects.get().coords,
            [{"x0": 20.0, "y0": 100.0, "x1": 330.0, "y1": 116.0}],
        )

    def _legacy_annotation(self):
        layer = Layer.objects.create(book=self.book, owner=self.user)
        return Annotation.objects.create(
            layer=layer,
            page_number=1,
            color="#FFEB3B",
            coords=[
                {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0},
                {"x0": 170.0, "y0": 100.0, "x1": 330.0, "y1": 116.0},
            ],
        )

    def test_command_merges_existing_rows(self):
        annotation = self._legacy_annotation()
        call_command("merge_annotation_rects", verbosity=0)
        annotation.refresh_from_db()
        self.assertEqual(
            annotation.coords,
            [{"x0": 20.0, "y0": 100.0, "x1": 330.0, "y1": 116.0}],
        )

    def test_command_dry_run_writes_nothing(self):
        annotation = self._legacy_annotation()
        before = list(annotation.coords)
        call_command("merge_annotation_rects", "--dry-run", verbosity=0)
        annotation.refresh_from_db()
        self.assertEqual(annotation.coords, before)

    def test_command_is_idempotent(self):
        annotation = self._legacy_annotation()
        call_command("merge_annotation_rects", verbosity=0)
        annotation.refresh_from_db()
        once = list(annotation.coords)
        call_command("merge_annotation_rects", verbosity=0)
        annotation.refresh_from_db()
        self.assertEqual(annotation.coords, once)

    def test_merge_is_stable_under_reapplication(self):
        rects = [
            {"x0": 20.0, "y0": 100.0, "x1": 180.0, "y1": 116.0},
            {"x0": 170.0, "y0": 100.0, "x1": 330.0, "y1": 116.0},
            {"x0": 20.0, "y0": 80.0, "x1": 90.0, "y1": 96.0},
        ]
        once = merge_rects(rects)
        self.assertEqual(merge_rects(once), once)


def make_pdf_book(owner, title, folder=None):
    return Book.objects.create(
        owner=owner,
        title=title,
        folder=folder,
        page_count=10,
        file=SimpleUploadedFile("s.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf"),
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class SharedLayerTestCase(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="vchytelka",
            email="vchytelka@example.com",
            password="parol-parol-42",
        )
        self.student = User.objects.create_user(
            username="uchen", email="uchen@example.com", password="parol-parol-42"
        )
        self.outsider = User.objects.create_user(
            username="storonnii", email="storonnii@example.com", password="parol-parol-42"
        )
        self.group = Group.objects.create(name="9-А", owner=self.teacher)
        GroupMembership.objects.create(group=self.group, user=self.student)

        self.book = make_pdf_book(self.teacher, "Географія")
        self.access = GroupAccess.objects.create(
            group=self.group, book=self.book, granted_by=self.teacher
        )
        self.layer = Layer.objects.create(book=self.book, owner=self.teacher)
        self.annotation = Annotation.objects.create(
            layer=self.layer,
            page_number=4,
            coords=[{"x0": 10, "y0": 20, "x1": 100, "y1": 40}],
            selected_text="Материки",
            note_text="Вивчити напам'ять",
            color="#A5D6A7",
        )
        self.publication = LayerPublication.objects.create(layer=self.layer, group=self.group)


class SharedLayerAccessTests(SharedLayerTestCase):
    def test_member_sees_published_layer(self):
        self.assertEqual(list(shared_layers(self.student)), [self.layer])

    def test_unpublished_layer_is_hidden(self):
        self.publication.delete()
        self.assertFalse(shared_layers(self.student).exists())

    def test_non_member_does_not_see_layer(self):
        self.assertFalse(shared_layers(self.outsider).exists())

    def test_anonymous_sees_nothing(self):
        self.assertFalse(shared_layers(AnonymousUser()).exists())

    def test_layer_disappears_with_book_access(self):
        self.access.delete()
        self.assertFalse(shared_layers(self.student).exists())

    def test_owner_does_not_get_own_layer_as_shared(self):
        GroupMembership.objects.create(group=self.group, user=self.teacher)
        self.assertFalse(shared_layers(self.teacher).exists())

    def test_publication_to_foreign_group_is_ignored(self):
        # Учасник групи сам «публікує» свій шар у чужу групу — учні його не бачать.
        classmate = User.objects.create_user(
            username="odnoklasnyk", email="o@example.com", password="parol-parol-42"
        )
        GroupMembership.objects.create(group=self.group, user=classmate)
        layer = Layer.objects.create(book=self.book, owner=classmate)
        LayerPublication.objects.create(layer=layer, group=self.group)

        self.assertEqual(list(shared_layers(self.student)), [self.layer])

    def test_catalog_book_needs_no_group_access(self):
        catalog = Folder.objects.create(owner=self.teacher, name="Каталог", is_catalog=True)
        book = make_pdf_book(self.teacher, "Атлас", folder=catalog)
        layer = Layer.objects.create(book=book, owner=self.teacher)
        LayerPublication.objects.create(layer=layer, group=self.group)

        self.assertIn(layer, shared_layers(self.student))


class ShareLinkLayerTests(SharedLayerTestCase):
    """Посилання «з нотатками» відкриває шар свого автора без членства в групі."""

    def _session(self, **link_fields):
        link_fields.setdefault("book", self.book)
        link = ShareLink.objects.create(
            token=f"tok-{ShareLink.objects.count()}", created_by=self.teacher, **link_fields
        )
        return {SESSION_TOKENS_KEY: [link.token]}

    def test_anonymous_sees_layer_via_link_with_annotations(self):
        session = self._session(include_annotations=True)
        self.assertEqual(list(shared_layers(AnonymousUser(), session)), [self.layer])

    def test_link_without_annotations_shows_no_layer(self):
        session = self._session()
        self.assertFalse(shared_layers(AnonymousUser(), session).exists())
        self.assertFalse(shared_layers(self.outsider, session).exists())

    def test_expired_link_shows_no_layer(self):
        session = self._session(
            include_annotations=True, expires_at=timezone.now() - timedelta(days=1)
        )
        self.assertFalse(shared_layers(AnonymousUser(), session).exists())

    def test_link_exposes_only_its_authors_layer(self):
        foreign = Layer.objects.create(book=self.book, owner=self.student)
        session = self._session(include_annotations=True)

        layers = list(shared_layers(self.outsider, session))
        self.assertEqual(layers, [self.layer])
        self.assertNotIn(foreign, layers)

    def test_folder_link_covers_subtree(self):
        root = Folder.objects.create(owner=self.teacher, name="Курс")
        child = Folder.objects.create(owner=self.teacher, name="Тема 2", parent=root)
        book = make_pdf_book(self.teacher, "Підручник", folder=child)
        layer = Layer.objects.create(book=book, owner=self.teacher)
        session = self._session(book=None, folder=root, include_annotations=True)

        self.assertEqual(list(shared_layers(AnonymousUser(), session)), [layer])

    def test_link_author_does_not_get_own_layer(self):
        session = self._session(include_annotations=True)
        self.assertFalse(shared_layers(self.teacher, session).exists())

    def test_link_and_group_publication_do_not_duplicate(self):
        session = self._session(include_annotations=True)
        self.assertEqual(list(shared_layers(self.student, session)), [self.layer])

    def test_without_session_links_are_ignored(self):
        self._session(include_annotations=True)
        self.assertFalse(shared_layers(self.outsider).exists())

    def test_reader_shows_layer_to_anonymous_after_share_view(self):
        ShareLink.objects.create(
            book=self.book, token="z-notatkamy", created_by=self.teacher, include_annotations=True
        )
        share = self.client.get(reverse("sharing:share_view", args=["z-notatkamy"]))
        self.assertContains(share, f"?layer={self.layer.pk}")

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertContains(response, 'id="layerSwitch"')
        self.assertEqual(
            [layer["id"] for layer in response.context["shared_layers_data"]], [self.layer.pk]
        )

    def test_reader_hides_layer_for_plain_link(self):
        ShareLink.objects.create(book=self.book, token="bez-notatok", created_by=self.teacher)
        share = self.client.get(reverse("sharing:share_view", args=["bez-notatok"]))
        self.assertNotContains(share, "?layer=")

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertNotContains(response, 'id="layerSwitch"')
        self.assertEqual(response.context["shared_layers_data"], [])


class PublishableGroupsTests(SharedLayerTestCase):
    def test_group_with_book_access(self):
        self.assertEqual(list(publishable_groups(self.teacher, self.book)), [self.group])

    def test_group_without_access_is_excluded(self):
        Group.objects.create(name="10-Б", owner=self.teacher)
        self.assertEqual(list(publishable_groups(self.teacher, self.book)), [self.group])

    def test_access_to_ancestor_folder_counts(self):
        root = Folder.objects.create(owner=self.teacher, name="Клас")
        child = Folder.objects.create(owner=self.teacher, name="Тема 1", parent=root)
        book = make_pdf_book(self.teacher, "Параграф", folder=child)
        other = Group.objects.create(name="10-В", owner=self.teacher)
        GroupAccess.objects.create(group=other, folder=root, granted_by=self.teacher)

        self.assertEqual(list(publishable_groups(self.teacher, book)), [other])

    def test_catalog_book_allows_all_own_groups(self):
        catalog = Folder.objects.create(owner=self.teacher, name="Каталог", is_catalog=True)
        book = make_pdf_book(self.teacher, "Атлас", folder=catalog)
        other = Group.objects.create(name="10-Г", owner=self.teacher)
        Group.objects.create(name="Чужа", owner=self.outsider)

        self.assertEqual(
            sorted(g.pk for g in publishable_groups(self.teacher, book)),
            sorted([self.group.pk, other.pk]),
        )

    def test_other_users_groups_are_excluded(self):
        self.assertFalse(publishable_groups(self.student, self.book).exists())


class LayerPublishViewTests(SharedLayerTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("annotations:layer_publish", args=[self.book.pk])
        self.client.force_login(self.teacher)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_publish_and_unpublish(self):
        self.publication.delete()
        self.client.post(self.url, {"groups": [self.group.pk]})
        self.assertTrue(LayerPublication.objects.filter(layer=self.layer, group=self.group).exists())

        self.client.post(self.url, {})
        self.assertFalse(LayerPublication.objects.filter(layer=self.layer).exists())

    def test_publish_creates_layer_when_missing(self):
        book = make_pdf_book(self.teacher, "Нова")
        GroupAccess.objects.create(group=self.group, book=book, granted_by=self.teacher)
        self.client.post(
            reverse("annotations:layer_publish", args=[book.pk]), {"groups": [self.group.pk]}
        )
        layer = Layer.objects.get(book=book, owner=self.teacher)
        self.assertEqual(list(layer.shared_with_groups.all()), [self.group])

    def test_group_without_book_access_rejected(self):
        other = Group.objects.create(name="Без доступу", owner=self.teacher)
        self.client.post(self.url, {"groups": [other.pk]})
        self.assertFalse(LayerPublication.objects.filter(group=other).exists())
        # Невалідна форма не чіпає наявних публікацій.
        self.assertTrue(LayerPublication.objects.filter(pk=self.publication.pk).exists())

    def test_foreign_group_rejected(self):
        foreign = Group.objects.create(name="Чужа", owner=self.outsider)
        GroupAccess.objects.create(group=foreign, book=self.book, granted_by=self.teacher)
        self.client.post(self.url, {"groups": [foreign.pk]})
        self.assertFalse(LayerPublication.objects.filter(group=foreign).exists())

    def test_invisible_book_is_404(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.url, {}).status_code, 404)


class SharedLayerApiTests(SharedLayerTestCase):
    def test_student_gets_layer_with_annotations(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse("annotations:api_book_shared_layers", args=[self.book.pk])
        )
        self.assertEqual(response.status_code, 200)
        layers = response.json()["layers"]
        self.assertEqual([layer["id"] for layer in layers], [self.layer.pk])
        self.assertEqual(layers[0]["owner"], "vchytelka")
        self.assertEqual(layers[0]["annotations"][0]["note"], "Вивчити напам'ять")

    def test_own_annotations_endpoint_stays_private(self):
        self.client.force_login(self.student)
        response = self.client.get(
            reverse("annotations:api_book_annotations", args=[self.book.pk])
        )
        self.assertEqual(response.json()["annotations"], [])

    def test_student_cannot_edit_or_delete_teacher_annotation(self):
        self.client.force_login(self.student)
        url = reverse("annotations:api_annotation_detail", args=[self.annotation.pk])
        patch = self.client.patch(
            url, data={"note_text": "зламано"}, content_type="application/json"
        )
        self.assertEqual(patch.status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.note_text, "Вивчити напам'ять")

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse("annotations:api_book_shared_layers", args=[self.book.pk])
        )
        self.assertEqual(response.status_code, 404)


class SharedLayerPageTests(SharedLayerTestCase):
    def test_reader_embeds_teacher_layer_for_student(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertContains(response, 'id="layerSwitch"')
        self.assertContains(response, 'id="shared-layers-data"')
        self.assertEqual(
            [layer["id"] for layer in response.context["shared_layers_data"]], [self.layer.pk]
        )

    def test_reader_has_no_switch_for_teacher(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        self.assertNotContains(response, 'id="layerSwitch"')
        self.assertEqual(response.context["shared_layers_data"], [])

    def test_book_detail_lists_teacher_layer_for_student(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        self.assertContains(response, "Нотатки вчителя")
        self.assertContains(response, f"?layer={self.layer.pk}")
        self.assertEqual(response.context["teacher_layers"][0].annotation_count, 1)
        self.assertIsNone(response.context["publish_form"])

    def test_book_detail_publish_form_for_group_owner(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        form = response.context["publish_form"]
        self.assertEqual(form.initial["groups"], [self.group.pk])
        self.assertContains(response, reverse("annotations:layer_publish", args=[self.book.pk]))


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class NoteGroupServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="avtor-tem", email="t@example.com", password="parol-parol-42"
        )
        self.book = make_pdf_book(self.user, "Фізика")
        self.other_book = make_pdf_book(self.user, "Хімія")

    def note(self, group=None, book=None):
        layer, _ = Layer.objects.get_or_create(book=book or self.book, owner=self.user)
        return Annotation.objects.create(
            layer=layer,
            group=group,
            page_number=1,
            coords=[{"x0": 1, "y0": 2, "x1": 30, "y1": 40}],
            color="#FFEB3B",
        )

    def test_create_makes_layer_and_strips_name(self):
        group = create_note_group(self.book, self.user, parse_group_name("  Формули  "))
        self.assertEqual(group.name, "Формули")
        self.assertEqual(group.layer.book, self.book)
        self.assertEqual(group.layer.owner, self.user)

    def test_duplicate_name_in_same_book_rejected(self):
        create_note_group(self.book, self.user, "Формули")
        with self.assertRaisesMessage(PayloadError, "тема з такою назвою вже є"):
            create_note_group(self.book, self.user, parse_group_name(" Формули "))

    def test_same_name_in_other_book_allowed(self):
        create_note_group(self.book, self.user, "Формули")
        create_note_group(self.other_book, self.user, "Формули")
        self.assertEqual(NoteGroup.objects.filter(name="Формули").count(), 2)

    def test_rename_to_existing_name_rejected(self):
        create_note_group(self.book, self.user, "Формули")
        second = create_note_group(self.book, self.user, "Задачі")
        with self.assertRaises(PayloadError):
            rename_note_group(second, "Формули")
        rename_note_group(second, "Задачі з підручника")
        second.refresh_from_db()
        self.assertEqual(second.name, "Задачі з підручника")

    def test_concurrent_duplicate_create_is_payload_error(self):
        # Інший запит устиг створити ту саму тему між перевіркою й INSERT.
        create_note_group(self.book, self.user, "Формули")
        with patch.object(QuerySet, "exists", return_value=False):
            with self.assertRaisesMessage(PayloadError, "тема з такою назвою вже є"):
                create_note_group(self.book, self.user, "Формули")
        # Транзакція жива: без savepoint тут було б «current transaction is aborted».
        self.assertEqual(NoteGroup.objects.filter(name="Формули").count(), 1)

    def test_concurrent_duplicate_rename_is_payload_error(self):
        create_note_group(self.book, self.user, "Формули")
        second = create_note_group(self.book, self.user, "Задачі")
        with patch.object(QuerySet, "exists", return_value=False):
            with self.assertRaisesMessage(PayloadError, "тема з такою назвою вже є"):
                rename_note_group(second, "Формули")
        self.assertEqual(second.name, "Задачі")
        self.assertEqual(NoteGroup.objects.get(pk=second.pk).name, "Задачі")

    def test_rename_to_same_name_is_noop(self):
        group = create_note_group(self.book, self.user, "Формули")
        rename_note_group(group, "Формули")
        self.assertEqual(NoteGroup.objects.get(pk=group.pk).name, "Формули")

    def test_parse_group_name_rejects_bad_input(self):
        for raw in (None, 5, "", "   ", "я" * 101):
            with self.subTest(raw=raw), self.assertRaises(PayloadError):
                parse_group_name(raw)

    def test_delete_keeps_notes_and_bumps_updated_at(self):
        group = create_note_group(self.book, self.user, "Формули")
        note = self.note(group)
        past = timezone.now() - timedelta(days=3)
        Annotation.objects.filter(pk=note.pk).update(updated_at=past)

        delete_note_group(group)

        note.refresh_from_db()
        self.assertIsNone(note.group)
        self.assertGreater(note.updated_at, past)
        self.assertFalse(NoteGroup.objects.filter(pk=group.pk).exists())

    def test_group_fields_absent_and_null(self):
        self.assertEqual(group_fields({}, self.book.pk, self.user), {})
        self.assertEqual(group_fields({"group": None}, self.book.pk, self.user), {"group": None})

    def test_group_fields_resolves_own_group(self):
        group = create_note_group(self.book, self.user, "Формули")
        self.assertEqual(
            group_fields({"group": group.pk}, self.book.pk, self.user), {"group": group}
        )

    def test_group_fields_rejects_other_book_and_foreign_owner(self):
        other_book_group = create_note_group(self.other_book, self.user, "Чужа книга")
        stranger = User.objects.create_user(
            username="chuzhyi", email="c@example.com", password="parol-parol-42"
        )
        foreign_group = create_note_group(self.book, stranger, "Чужий шар")
        for group in (other_book_group, foreign_group):
            with self.subTest(group=group.name), self.assertRaisesMessage(
                PayloadError, "group: теми нема або вона з іншої книги"
            ):
                group_fields({"group": group.pk}, self.book.pk, self.user)

    def test_group_fields_rejects_bad_type(self):
        for raw in ("1", True, 0, -3, 1.5):
            with self.subTest(raw=raw), self.assertRaises(PayloadError):
                group_fields({"group": raw}, self.book.pk, self.user)

    def test_group_fields_client_id(self):
        group = create_note_group(self.book, self.user, "Офлайн")
        other = create_note_group(self.other_book, self.user, "Офлайн")
        client_groups = {"t1": group, "t2": other}

        self.assertEqual(
            group_fields({"group_client_id": "t1"}, self.book.pk, self.user, client_groups),
            {"group": group},
        )
        for client_id in ("t2", "nema", ["t1"]):
            with self.subTest(client_id=client_id), self.assertRaises(PayloadError):
                group_fields(
                    {"group_client_id": client_id}, self.book.pk, self.user, client_groups
                )


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class NoteGroupApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api-tem", email="at@example.com", password="parol-parol-42"
        )
        self.stranger = User.objects.create_user(
            username="api-chuzhyi", email="ac@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = make_pdf_book(self.user, "Біологія")
        self.other_book = make_pdf_book(self.user, "Географія")
        self.list_url = reverse("annotations:api_book_note_groups", args=[self.book.pk])

    def detail_url(self, group):
        return reverse("annotations:api_note_group_detail", args=[group.pk])

    def post_annotation(self, **extra):
        payload = {
            "page_number": 2,
            "coords": [{"x0": 72.0, "y0": 100.0, "x1": 300.0, "y1": 118.0}],
            "color": "#FFEB3B",
            **extra,
        }
        return self.client.post(
            reverse("annotations:api_book_annotations", args=[self.book.pk]),
            data=payload,
            content_type="application/json",
        )

    def test_create_and_list(self):
        response = self.client.post(
            self.list_url, data={"name": " Еволюція "}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        group = NoteGroup.objects.get()
        self.assertEqual(response.json(), {"id": group.pk, "name": "Еволюція"})

        listing = self.client.get(self.list_url).json()
        self.assertEqual(listing, {"groups": [{"id": group.pk, "name": "Еволюція"}]})

    def test_duplicate_and_empty_name_are_400(self):
        create_note_group(self.book, self.user, "Еволюція")
        for name in ("Еволюція", "  ", None):
            with self.subTest(name=name):
                response = self.client.post(
                    self.list_url, data={"name": name}, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("name:", response.json()["error"])

    def test_non_object_body_is_400(self):
        response = self.client.post(self.list_url, data=[1], content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_list_shows_only_own_groups_of_this_book(self):
        create_note_group(self.other_book, self.user, "Інша книга")
        create_note_group(self.book, self.stranger, "Чужа")
        self.assertEqual(self.client.get(self.list_url).json(), {"groups": []})

    def test_invisible_book_is_404(self):
        hidden = make_pdf_book(self.stranger, "Чужа книга")
        url = reverse("annotations:api_book_note_groups", args=[hidden.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_rename_and_delete(self):
        group = create_note_group(self.book, self.user, "Еволюція")
        note = self.post_annotation(group=group.pk).json()

        response = self.client.patch(
            self.detail_url(group), data={"name": "Еволюція ссавців"},
            content_type="application/json",
        )
        self.assertEqual(response.json(), {"id": group.pk, "name": "Еволюція ссавців"})

        self.assertEqual(self.client.delete(self.detail_url(group)).status_code, 204)
        self.assertIsNone(Annotation.objects.get(pk=note["id"]).group)

    def test_foreign_group_detail_is_404(self):
        group = create_note_group(self.book, self.stranger, "Чужа")
        url = self.detail_url(group)
        patch = self.client.patch(url, data={"name": "x"}, content_type="application/json")
        self.assertEqual(patch.status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(NoteGroup.objects.filter(pk=group.pk).exists())

    def test_annotation_created_into_group(self):
        group = create_note_group(self.book, self.user, "Еволюція")
        response = self.post_annotation(group=group.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["group"], group.pk)
        self.assertEqual(Annotation.objects.get().group, group)

    def test_annotation_without_group_is_ungrouped(self):
        self.assertIsNone(self.post_annotation().json()["group"])

    def test_annotation_with_group_of_other_book_is_400(self):
        group = create_note_group(self.other_book, self.user, "Інша книга")
        response = self.post_annotation(group=group.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("group:", response.json()["error"])
        self.assertFalse(Annotation.objects.exists())

    def test_patch_moves_annotation_between_groups(self):
        first = create_note_group(self.book, self.user, "Перша")
        second = create_note_group(self.book, self.user, "Друга")
        note = self.post_annotation(group=first.pk).json()
        url = reverse("annotations:api_annotation_detail", args=[note["id"]])

        moved = self.client.patch(url, data={"group": second.pk}, content_type="application/json")
        self.assertEqual(moved.json()["group"], second.pk)

        cleared = self.client.patch(url, data={"group": None}, content_type="application/json")
        self.assertIsNone(cleared.json()["group"])

    def test_patch_to_foreign_group_is_400(self):
        note = self.post_annotation().json()
        foreign = create_note_group(self.book, self.stranger, "Чужа")
        url = reverse("annotations:api_annotation_detail", args=[note["id"]])
        response = self.client.patch(url, data={"group": foreign.pk}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(Annotation.objects.get(pk=note["id"]).group)


class ShareLinkTopicTests(SharedLayerTestCase):
    """Посилання на книгу з обраними темами відкриває лише нотатки цих тем."""

    def setUp(self):
        super().setUp()
        self.publication.delete()  # перевіряємо саме посилання
        self.formulas = NoteGroup.objects.create(layer=self.layer, name="Формули")
        self.answers = NoteGroup.objects.create(layer=self.layer, name="Відповіді")
        self.in_formulas = self._note(self.formulas, "E = mc²", page=5)
        self.in_answers = self._note(self.answers, "Відповідь: 42", page=6)
        # self.annotation з SharedLayerTestCase лишається «Без групи».

    def _note(self, group, text, page=5):
        return Annotation.objects.create(
            layer=self.layer,
            group=group,
            page_number=page,
            coords=[{"x0": 10, "y0": 20, "x1": 100, "y1": 40}],
            selected_text=text,
            color="#FFEB3B",
        )

    def _link(self, *groups, **fields):
        fields.setdefault("book", self.book)
        link = ShareLink.objects.create(
            token=f"tema-{ShareLink.objects.count()}",
            created_by=self.teacher,
            include_annotations=True,
            annotation_scope=(
                ShareLink.AnnotationScope.SELECTED if groups else ShareLink.AnnotationScope.ALL
            ),
            **fields,
        )
        link.note_groups.set(groups)
        return link

    def _session(self, *links):
        return {SESSION_TOKENS_KEY: [link.token for link in links]}

    def visible(self, session, user=None):
        return set(
            shared_annotations(user or AnonymousUser(), session).values_list("pk", flat=True)
        )

    def test_all_scope_shows_every_note(self):
        session = self._session(self._link())
        self.assertEqual(
            self.visible(session),
            {self.annotation.pk, self.in_formulas.pk, self.in_answers.pk},
        )

    def test_selected_scope_shows_only_chosen_topics(self):
        session = self._session(self._link(self.formulas))
        self.assertEqual(self.visible(session), {self.in_formulas.pk})

    def test_note_moved_out_of_topic_disappears_and_moved_in_appears(self):
        session = self._session(self._link(self.formulas))
        self.in_formulas.group = self.answers
        self.in_formulas.save()
        self.annotation.group = self.formulas
        self.annotation.save()
        self.assertEqual(self.visible(session), {self.annotation.pk})

    def test_deleting_all_chosen_topics_shows_nothing(self):
        link = self._link(self.formulas)
        delete_note_group(self.formulas)
        link.refresh_from_db()
        self.assertEqual(link.annotation_scope, ShareLink.AnnotationScope.SELECTED)
        self.assertEqual(self.visible(self._session(link)), set())

    def test_two_links_union_topics(self):
        session = self._session(self._link(self.formulas), self._link(self.answers))
        self.assertEqual(self.visible(session), {self.in_formulas.pk, self.in_answers.pk})

    def test_group_publication_overrides_link_restriction(self):
        LayerPublication.objects.create(layer=self.layer, group=self.group)
        session = self._session(self._link(self.formulas))
        self.assertEqual(
            self.visible(session, user=self.student),
            {self.annotation.pk, self.in_formulas.pk, self.in_answers.pk},
        )

    def test_folder_link_ignores_topic_selection(self):
        folder = Folder.objects.create(owner=self.teacher, name="Курс")
        self.book.folder = folder
        self.book.save()
        session = self._session(self._link(self.formulas, book=None, folder=folder))
        self.assertEqual(len(self.visible(session)), 3)

    def test_link_without_annotations_shows_nothing(self):
        link = self._link(self.formulas)
        link.include_annotations = False
        link.save()
        self.assertEqual(self.visible(self._session(link)), set())

    def test_reader_embeds_only_chosen_topics_without_group_key(self):
        link = self._link(self.formulas)
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(reverse("book_library:book_reader", args=[self.book.pk]))
        layers = response.context["shared_layers_data"]
        self.assertEqual([layer["id"] for layer in layers], [self.layer.pk])
        notes = layers[0]["annotations"]
        self.assertEqual([note["id"] for note in notes], [self.in_formulas.pk])
        self.assertNotIn("group", notes[0])

    def test_shared_layers_api_respects_topics(self):
        self.client.force_login(self.outsider)
        link = self._link(self.answers)
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(
            reverse("annotations:api_book_shared_layers", args=[self.book.pk])
        )
        notes = response.json()["layers"][0]["annotations"]
        self.assertEqual([note["id"] for note in notes], [self.in_answers.pk])

    def test_book_detail_counts_only_visible_notes_across_pages(self):
        self._note(self.formulas, "F = ma", page=8)  # друга нотатка теми, інша сторінка
        self.client.force_login(self.outsider)
        link = self._link(self.formulas)
        self.client.get(reverse("sharing:share_view", args=[link.token]))

        response = self.client.get(reverse("book_library:book_detail", args=[self.book.pk]))
        self.assertEqual(response.context["teacher_layers"][0].annotation_count, 2)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class NoteGroupPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="storinka-tem", email="st@example.com", password="parol-parol-42"
        )
        self.client.force_login(self.user)
        self.book = make_pdf_book(self.user, "Історія")
        self.layer = Layer.objects.create(book=self.book, owner=self.user)
        self.cossacks = NoteGroup.objects.create(layer=self.layer, name="Козацтво")
        self.empty = NoteGroup.objects.create(layer=self.layer, name="Порожня")
        self.grouped = self._note(self.cossacks, "Січ", page=3)
        self.loose = self._note(None, "Дата", page=1)
        self.url = reverse("annotations:book_annotations", args=[self.book.pk])

    def _note(self, group, text, page):
        return Annotation.objects.create(
            layer=self.layer, group=group, page_number=page,
            coords=[{"x0": 1, "y0": 2, "x1": 30, "y1": 40}],
            selected_text=text, color="#FFEB3B",
        )

    def test_sections_by_topic_then_ungrouped(self):
        response = self.client.get(self.url)
        sections = response.context["sections"]
        self.assertEqual(
            [(s["group"].name if s["group"] else None, s["count"]) for s in sections],
            [("Козацтво", 1), ("Порожня", 0), (None, 1)],
        )
        self.assertTrue(response.context["show_group_headings"])
        self.assertContains(response, "Без групи")
        self.assertContains(response, "У темі поки нема нотаток.")
        self.assertEqual(response.context["pages_with_notes"], 2)

    def test_no_topics_no_headings(self):
        NoteGroup.objects.all().delete()
        response = self.client.get(self.url)
        self.assertFalse(response.context["show_group_headings"])
        self.assertNotContains(response, "Без групи")
        self.assertContains(response, "Сторінка 1")

    def test_rename(self):
        url = reverse("annotations:note_group_rename", args=[self.cossacks.pk])
        response = self.client.post(url, {"name": " Козацька доба "})
        self.assertRedirects(response, self.url)
        self.cossacks.refresh_from_db()
        self.assertEqual(self.cossacks.name, "Козацька доба")

    def test_rename_to_duplicate_shows_error(self):
        url = reverse("annotations:note_group_rename", args=[self.empty.pk])
        response = self.client.post(url, {"name": "Козацтво"}, follow=True)
        self.assertContains(response, "тема з такою назвою вже є")
        self.empty.refresh_from_db()
        self.assertEqual(self.empty.name, "Порожня")

    def test_delete_moves_notes_to_ungrouped(self):
        url = reverse("annotations:note_group_delete", args=[self.cossacks.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)
        self.assertRedirects(response, self.url)
        self.grouped.refresh_from_db()
        self.assertIsNone(self.grouped.group)

    def test_foreign_topic_is_404(self):
        stranger = User.objects.create_user(
            username="chuzhyi2", email="c2@example.com", password="parol-parol-42"
        )
        self.client.force_login(stranger)
        rename = reverse("annotations:note_group_rename", args=[self.cossacks.pk])
        delete = reverse("annotations:note_group_delete", args=[self.cossacks.pk])
        self.assertEqual(self.client.post(rename, {"name": "x"}).status_code, 404)
        self.assertEqual(self.client.post(delete).status_code, 404)
        self.assertTrue(NoteGroup.objects.filter(pk=self.cossacks.pk).exists())


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
class ReaderTopicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="chytach-tem", email="ct@example.com", password="parol-parol-42"
        )
        self.book = make_pdf_book(self.user, "Біологія")
        self.group = create_note_group(self.book, self.user, "Еволюція")
        self.url = reverse("book_library:book_reader", args=[self.book.pk])

    def test_reader_embeds_topics_and_urls(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(
            response.context["note_groups_data"], [{"id": self.group.pk, "name": "Еволюція"}]
        )
        self.assertContains(response, 'id="topicSwitch"')
        self.assertContains(response, 'id="note-groups-data"')
        # Створення теми — окрема помітна кнопка, теми — чипи, а не пункт у випадаючому списку.
        self.assertContains(response, "data-topic-add")
        self.assertContains(response, f'data-topic="{self.group.pk}"')
        self.assertContains(response, "data-popover-topic-chips")
        self.assertContains(response, "data-popover-topic-form")
        self.assertNotContains(response, "<select")
        self.assertContains(
            response,
            f'data-note-groups-url="{reverse("annotations:api_book_note_groups", args=[self.book.pk])}"',
        )
        self.assertContains(response, f'data-book-id="{self.book.pk}"')

    def test_anonymous_reader_has_no_topic_switch(self):
        ShareLink.objects.create(book=self.book, token="anon-tema", created_by=self.user)
        self.client.get(reverse("sharing:share_view", args=["anon-tema"]))
        response = self.client.get(self.url)
        self.assertNotContains(response, 'id="topicSwitch"')
        self.assertEqual(response.context["note_groups_data"], [])
