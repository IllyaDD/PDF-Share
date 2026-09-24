import shutil
import tempfile

from book_library.models import Book
from book_sharing_settings.testing import FILESYSTEM_STORAGES
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from users.models import User

from .models import Annotation, Layer
from .validators import merge_rects, parse_coords

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="pdfplus-test-media-")


def tearDownModule():
    shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


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
            "задовга нотатка": {"note_text": "я" * 2001},
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

    def test_anonymous_cannot_create(self):
        self.client.logout()
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Annotation.objects.exists())

    def test_cannot_annotate_invisible_book(self):
        stranger = User.objects.create_user(
            username="chuzhak-notatok", email="cn@example.com", password="parol-parol-42"
        )
        self.client.force_login(stranger)
        self.assertEqual(self.post().status_code, 404)

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
        self.assertEqual(self.client.put(self.url, data="{}",
                                         content_type="application/json").status_code, 405)


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
