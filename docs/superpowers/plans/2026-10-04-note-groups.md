# Теми нотаток — план реалізації

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Користувач розкладає нотатки книги за темами, а вчитель може поширити за посиланням на книгу лише обрані теми.

**Architecture:** Нова модель `NoteGroup` (FK на `Layer`), `Annotation.group` (FK, `SET_NULL`), на `ShareLink` — явний `annotation_scope` + M2M `note_groups`. Чужі нотатки читаються лише через нову `annotations/access.shared_annotations()`, яка фільтрує на рівні анотації. Бізнес-операції з темами (створити, перейменувати, видалити, розв'язати `group` з payload) живуть в `annotations/groups.py` і спільні для вебу та DRF-синку.

**Tech Stack:** Django 6.1, PostgreSQL, DRF + SimpleJWT, vanilla JS (ES-модуль `reader.js`), Bootstrap 5, crispy-forms.

**Spec:** `docs/superpowers/specs/2026-10-04-note-groups-design.md`

## Global Constraints

- Усі команди `manage.py` — з теки `PDF+/`: `cd "/Users/illyadd/Prods/MAN-PDF-Share-Prod/PDF+"`.
- Тести — Django-раннер: `uv run python manage.py test <label>` (pytest у проєкті нема).
- Коментарі, docstring-и, `verbose_name`, повідомлення користувачу, тексти шаблонів — українською; ідентифікатори, поля, URL name — англійською.
- Кожна нова модель явно задає `Meta.db_table` (`annotations_notegroup`).
- Тестові класи, що створюють `Book`, мають `@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)` (уже є в модулях тестів — використовуй наявний `TEST_MEDIA_ROOT`).
- Будь-яке читання `Book` — через `visible_books()`; чужих нотаток — лише через `shared_annotations()`.
- Нових URL у `reader.js` не хардкодити — лише через `data-`атрибути `#pdf-viewer`.
- Назва теми: `strip()`, 1..100 символів, унікальна в межах шару (`UniqueConstraint(layer, name)`).
- `ShareLink.annotation_scope`: `"all"` | `"selected"`; наявні посилання — `"all"`.
- Видаляти тему лише через `annotations.groups.delete_note_group()` (оновлює `updated_at` звільнених нотаток).
- **Коміти:** робоче дерево на `master` містить багато незакомічених змін користувача в тих самих файлах. Не комітити без явного дозволу користувача; замість кроку «Commit» кожна задача закінчується чекпойнтом — прогоном тестів застосунку.

## Review Focus

1. Посилання `?annotation=<id>` на власну нотатку, коли в `localStorage` запам'ятована інша тема — нотатка має відкритись (тема скидається на «Усі теми»). Перевіряється в Task 6, крок ручної перевірки.
2. Назва з пробілами або дубль після `strip()` (`" Формули "` при наявній «Формули») — відхиляється з 400, а не падає на `IntegrityError`. Тест у Task 1 і Task 7 (перейменування через push у дубль не валить транзакцію батча).
3. Лічильник нотаток учителя на сторінці книги при `SELECTED`-посиланні з кількома нотатками на різних сторінках — показує точну суму, а не розбиту GROUP BY за `Meta.ordering`. Тест у Task 3.
4. Підроблений POST форми посилання з темою іншої книги або чужою темою — відхиляється. Тест у Task 4.
5. `group` / `group_client_id` нотатки, що вказує на тему іншої книги (у вебі та в синку), — 400 / помилка операції, нотатка не створюється. Тести в Task 2 і Task 7.

---

## Файлова структура

| Файл | Відповідальність |
|---|---|
| `annotations/models.py` | + `NoteGroup`, + `Annotation.group` |
| `annotations/migrations/0005_notegroup.py` | згенерована |
| `annotations/validators.py` | + `parse_group_name`, `parse_group_id`; `parse_annotation_update` пропускає `group` |
| `annotations/groups.py` (новий) | `create_note_group`, `rename_note_group`, `delete_note_group`, `group_fields` |
| `annotations/serializers.py` | + `serialize_note_group`; `serialize_annotation` + `group`; `serialize_shared_layers(layers, annotations)` |
| `annotations/api.py`, `annotations/urls.py` | JSON-ендпоінти тем; `group` у POST/PATCH анотацій |
| `annotations/access.py` | + `shared_annotations()`, спільний `_link_layer_scope()` |
| `annotations/views.py` + `annotation_list.html` | групування за темами, перейменування/видалення |
| `sharing/models.py`, `sharing/migrations/0006_sharelink_note_groups.py` | `annotation_scope`, `note_groups` |
| `sharing/forms.py`, `sharing/views.py`, `link_form.html`, `link_list.html` | вибір тем у посиланні |
| `book_library/views.py` | контекст читалки (`note_groups_data`, фільтровані чужі шари), лічильник у `book_detail` |
| `book_library/templates/book_library/book_reader.html`, `static/js/reader.js` | перемикач тем, тема в поповері |
| `api/serializers.py`, `api/sync.py` | `note_groups` у pull/push, `group` в анотаціях |
| `CLAUDE.md`, `README.md` | документація |

---

### Task 1: Модель `NoteGroup` і сервіс тем

**Files:**
- Modify: `PDF+/annotations/models.py`
- Create: `PDF+/annotations/migrations/0005_notegroup.py` (через `makemigrations`)
- Modify: `PDF+/annotations/validators.py`
- Create: `PDF+/annotations/groups.py`
- Test: `PDF+/annotations/tests.py` (новий клас `NoteGroupServiceTests` у кінці файлу)

**Interfaces:**
- Produces:
  - `NoteGroup(layer, name, created_at, updated_at)`, `related_name="note_groups"`; `Annotation.group` (`related_name="annotations"`).
  - `parse_group_name(raw) -> str`, `parse_group_id(raw) -> int | None`, `MAX_GROUP_NAME_LENGTH = 100` в `annotations/validators.py`.
  - `annotations.groups.create_note_group(book, owner, name) -> NoteGroup`
  - `annotations.groups.rename_note_group(group, name) -> NoteGroup`
  - `annotations.groups.delete_note_group(group) -> None`
  - `annotations.groups.group_fields(payload, book_id, owner, client_groups=None) -> dict` — `{}` | `{"group": NoteGroup | None}`; кидає `PayloadError`.

- [ ] **Step 1: Write the failing tests**

Додай імпорти на початку `PDF+/annotations/tests.py` (до наявних):

```python
from .groups import create_note_group, delete_note_group, group_fields, rename_note_group
from .models import Annotation, Layer, LayerPublication, NoteGroup
from .validators import MAX_NOTE_LENGTH, PayloadError, merge_rects, parse_coords, parse_group_name
```

(заміни наявні рядки `from .models import ...` і `from .validators import ...` на ці).

У кінець файлу:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test annotations.tests.NoteGroupServiceTests`
Expected: FAIL — `ImportError: cannot import name 'create_note_group'` (або `NoteGroup`).

- [ ] **Step 3: Add the model**

У `PDF+/annotations/models.py` після класу `LayerPublication` (перед `class Annotation`):

```python
class NoteGroup(models.Model):
    """Тема нотаток у межах шару: одна книга, один власник.

    Видаляти лише через annotations.groups.delete_note_group() — інакше SET_NULL
    не оновить updated_at нотаток, і дельта синку їх не побачить.
    """

    layer = models.ForeignKey(
        Layer,
        on_delete=models.CASCADE,
        related_name="note_groups",
    )
    name = models.CharField("назва", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "annotations_notegroup"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["layer", "name"],
                name="unique_note_group_per_layer",
            )
        ]

    def __str__(self):
        return self.name
```

У `class Annotation` після поля `color`:

```python
    # Тема нотатки; null — «Без групи».
    group = models.ForeignKey(
        NoteGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotations",
    )
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations annotations --name notegroup`
Expected: `annotations/migrations/0005_notegroup.py` — `Create model NoteGroup`, `Add field group to annotation`.

- [ ] **Step 5: Add validators**

У `PDF+/annotations/validators.py` після `MAX_SELECTED_TEXT_LENGTH = 5000`:

```python
MAX_GROUP_NAME_LENGTH = 100
```

Після функції `parse_text`:

```python
def parse_group_name(raw):
    if not isinstance(raw, str):
        raise PayloadError("name: очікується рядок")
    name = raw.strip()
    if not name:
        raise PayloadError("name: назва теми не може бути порожньою")
    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise PayloadError(f"name: задовга назва (максимум {MAX_GROUP_NAME_LENGTH})")
    return name


def parse_group_id(raw):
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise PayloadError("group: очікується id теми або null")
    return raw
```

У `parse_annotation_update` заміни рядок `allowed = {"color", "note_text"}` на:

```python
    # group перевіряє groups.group_fields(): для неї потрібні книга й власник.
    allowed = {"color", "note_text", "group"}
```

- [ ] **Step 6: Create the service module**

`PDF+/annotations/groups.py`:

```python
"""Операції з темами нотаток — спільні для веб-API, сторінки нотаток і DRF-синку."""

from django.db import transaction
from django.utils import timezone

from .models import Layer, NoteGroup
from .validators import PayloadError, parse_group_id

DUPLICATE_NAME = "name: тема з такою назвою вже є"


def create_note_group(book, owner, name):
    layer, _ = Layer.objects.get_or_create(book=book, owner=owner)
    # Перевірка до INSERT: IntegrityError зламав би транзакцію батча синку.
    if layer.note_groups.filter(name=name).exists():
        raise PayloadError(DUPLICATE_NAME)
    return NoteGroup.objects.create(layer=layer, name=name)


def rename_note_group(group, name):
    if NoteGroup.objects.filter(layer_id=group.layer_id, name=name).exclude(pk=group.pk).exists():
        raise PayloadError(DUPLICATE_NAME)
    group.name = name
    group.save(update_fields=["name", "updated_at"])
    return group


@transaction.atomic
def delete_note_group(group):
    """Нотатки лишаються «Без групи» з новим updated_at — так їх побачить дельта синку."""
    group.annotations.update(group=None, updated_at=timezone.now())
    group.delete()


def group_fields(payload, book_id, owner, client_groups=None):
    """Поле group для створення чи зміни нотатки.

    Тема має належати шару owner на книзі book_id. client_groups — теми, створені
    в цьому ж пуші синку: {client_id: NoteGroup}.
    """
    if client_groups is not None and "group_client_id" in payload:
        try:
            group = client_groups[payload["group_client_id"]]
        except (KeyError, TypeError):
            raise PayloadError("group_client_id: такої теми нема в цьому пуші")
        if group.layer.book_id != book_id:
            raise PayloadError("group_client_id: тема з іншої книги")
        return {"group": group}

    if "group" not in payload:
        return {}
    group_id = parse_group_id(payload["group"])
    if group_id is None:
        return {"group": None}
    group = NoteGroup.objects.filter(
        pk=group_id, layer__book_id=book_id, layer__owner=owner
    ).first()
    if group is None:
        raise PayloadError("group: теми нема або вона з іншої книги")
    return {"group": group}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python manage.py test annotations.tests.NoteGroupServiceTests`
Expected: PASS (13 tests).

- [ ] **Step 8: Checkpoint**

Run: `uv run python manage.py test annotations api && uv run python manage.py makemigrations --check --dry-run`
Expected: усе зелене; `No changes detected`.

---

### Task 2: Веб-API тем і поле `group` в анотаціях

**Files:**
- Modify: `PDF+/annotations/serializers.py`
- Modify: `PDF+/annotations/api.py`
- Modify: `PDF+/annotations/urls.py`
- Test: `PDF+/annotations/tests.py` (новий клас `NoteGroupApiTests`)

**Interfaces:**
- Consumes: Task 1 — `create_note_group`, `rename_note_group`, `delete_note_group`, `group_fields`, `parse_group_name`, `NoteGroup`.
- Produces:
  - `serialize_note_group(group) -> {"id": int, "name": str}`
  - `serialize_annotation(a)` додає ключ `"group": int | None`
  - URL names `annotations:api_book_note_groups` (`api/books/<book_pk>/note-groups/`), `annotations:api_note_group_detail` (`api/note-groups/<pk>/`).

- [ ] **Step 1: Write the failing tests**

У кінець `PDF+/annotations/tests.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test annotations.tests.NoteGroupApiTests`
Expected: FAIL — `NoReverseMatch: Reverse for 'api_book_note_groups' not found`.

- [ ] **Step 3: Serializers**

`PDF+/annotations/serializers.py` — у `serialize_annotation` додай ключ останнім:

```python
        "group": annotation.group_id,
```

і нову функцію після `serialize_annotation`:

```python
def serialize_note_group(group):
    return {"id": group.pk, "name": group.name}
```

- [ ] **Step 4: API views**

У `PDF+/annotations/api.py` заміни імпорти моделей/серіалізаторів/валідаторів і додай нові:

```python
from .access import shared_layers
from .groups import create_note_group, delete_note_group, group_fields, rename_note_group
from .models import Annotation, Layer, NoteGroup
from .serializers import serialize_annotation, serialize_note_group, serialize_shared_layers
from .validators import (
    PayloadError,
    parse_annotation_update,
    parse_group_name,
    parse_new_annotation,
)
```

Після `_json_body` додай:

```python
def _json_object(request):
    payload = _json_body(request)
    if not isinstance(payload, dict):
        raise PayloadError("очікується JSON-об'єкт")
    return payload
```

У `book_annotations` заміни POST-гілку (від `try:` до `return JsonResponse(... status=201)`):

```python
    try:
        payload = _json_body(request)
        fields = parse_new_annotation(payload, book.page_count)
        fields.update(group_fields(payload, book.pk, request.user))
    except PayloadError as error:
        return _bad_request(error)

    layer, _ = Layer.objects.get_or_create(book=book, owner=request.user)
    annotation = Annotation.objects.create(layer=layer, **fields)

    return JsonResponse(serialize_annotation(annotation), status=201)
```

У `annotation_detail` заміни `get_object_or_404(...)` і PATCH-валідацію:

```python
    annotation = get_object_or_404(
        Annotation.objects.select_related("layer"), pk=pk, layer__owner=request.user
    )
```

```python
    try:
        payload = _json_body(request)
        fields = parse_annotation_update(payload)
        fields.update(group_fields(payload, annotation.layer.book_id, request.user))
    except PayloadError as error:
        return _bad_request(error)
```

(`parse_annotation_update` першим перевіряє, що payload — dict, тож `group_fields` отримує вже словник.)

У кінець файлу:

```python
@login_required
@require_http_methods(["GET", "POST"])
def book_note_groups(request, book_pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)

    if request.method == "GET":
        groups = NoteGroup.objects.filter(layer__book=book, layer__owner=request.user)
        return JsonResponse({"groups": [serialize_note_group(g) for g in groups]})

    try:
        name = parse_group_name(_json_object(request).get("name"))
        group = create_note_group(book, request.user, name)
    except PayloadError as error:
        return _bad_request(error)
    return JsonResponse(serialize_note_group(group), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def note_group_detail(request, pk):
    group = get_object_or_404(NoteGroup, pk=pk, layer__owner=request.user)

    if request.method == "DELETE":
        delete_note_group(group)
        return JsonResponse({}, status=204)

    try:
        rename_note_group(group, parse_group_name(_json_object(request).get("name")))
    except PayloadError as error:
        return _bad_request(error)
    return JsonResponse(serialize_note_group(group))
```

- [ ] **Step 5: URLs**

У `PDF+/annotations/urls.py` після рядка `api_annotation_detail`:

```python
    path("api/books/<int:book_pk>/note-groups/", api.book_note_groups, name="api_book_note_groups"),
    path("api/note-groups/<int:pk>/", api.note_group_detail, name="api_note_group_detail"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python manage.py test annotations.tests.NoteGroupApiTests annotations.tests.AnnotationApiTests annotations.tests.AnnotationApiUpdateTests`
Expected: PASS. Якщо наявний тест порівнює `serialize_annotation` цілим словником — додай у очікуване `"group": None`.

- [ ] **Step 7: Checkpoint**

Run: `uv run python manage.py test annotations book_library`
Expected: усе зелене.

---

### Task 3: Доступ — `shared_annotations()` і поля посилання

**Files:**
- Modify: `PDF+/sharing/models.py`
- Create: `PDF+/sharing/migrations/0006_sharelink_note_groups.py` (через `makemigrations`)
- Modify: `PDF+/annotations/access.py`
- Modify: `PDF+/annotations/serializers.py` (`serialize_shared_layers`)
- Modify: `PDF+/annotations/api.py` (`book_shared_layers`)
- Modify: `PDF+/book_library/views.py` (`book_detail_view`, `book_reader_view` — лише чужі шари)
- Test: `PDF+/annotations/tests.py` (новий клас `ShareLinkTopicTests`)

**Interfaces:**
- Consumes: Task 1 — `NoteGroup`, `delete_note_group`; Task 2 — `serialize_annotation` з ключем `group`.
- Produces:
  - `ShareLink.AnnotationScope.ALL` / `.SELECTED`, `ShareLink.annotation_scope`, `ShareLink.note_groups` (M2M на `annotations.NoteGroup`, `related_name="share_links"`).
  - `annotations.access.shared_annotations(user, session=None) -> QuerySet[Annotation]` (distinct).
  - `serialize_shared_layers(layers, annotations) -> list[dict]` — анотації без ключа `group`.

- [ ] **Step 1: Write the failing tests**

У імпорт `from .access import publishable_groups, shared_layers` додай `shared_annotations`. У кінець `PDF+/annotations/tests.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test annotations.tests.ShareLinkTopicTests`
Expected: FAIL — `ImportError: cannot import name 'shared_annotations'`.

- [ ] **Step 3: ShareLink fields + migration**

У `PDF+/sharing/models.py`, у `class ShareLink` після `include_annotations`:

```python
    class AnnotationScope(models.TextChoices):
        ALL = "all", "Усі нотатки"
        SELECTED = "selected", "Лише обрані теми"

    # Явний режим, а не «порожній note_groups = усі»: якщо автор видалить усі обрані
    # теми, посилання має показувати нічого, а не весь шар.
    annotation_scope = models.CharField(
        "які нотатки", max_length=8, choices=AnnotationScope.choices, default=AnnotationScope.ALL
    )
    note_groups = models.ManyToManyField(
        "annotations.NoteGroup",
        blank=True,
        related_name="share_links",
        verbose_name="теми",
    )
```

Run: `uv run python manage.py makemigrations sharing --name sharelink_note_groups`
Expected: `sharing/migrations/0006_sharelink_note_groups.py` з залежністю від `('annotations', '0005_notegroup')`.

- [ ] **Step 4: Access**

У `PDF+/annotations/access.py` заміни імпорти й `_share_link_sources` та додай `shared_annotations`:

```python
from functools import reduce
from operator import or_

from django.db.models import F, Q

from book_library.access import folder_subtree_ids, live_share_links, visible_books
from sharing.models import Group, ShareLink

from .models import Annotation, Layer


def _annotation_links(session):
    return [link for link in live_share_links(session) if link.include_annotations]


def _link_layer_scope(link, prefix=""):
    """Шар автора посилання на книзі посилання чи в піддереві теки; prefix — шлях до Layer."""
    if link.book_id:
        scope = Q(**{f"{prefix}book_id": link.book_id})
    else:
        scope = Q(**{f"{prefix}book__folder_id__in": folder_subtree_ids(link.folder_id)})
    return scope & Q(**{f"{prefix}owner_id": link.created_by_id})


def _share_link_sources(session):
    """Шари авторів посилань «з нотатками» — на книгах, які ці посилання відкривають."""
    return [_link_layer_scope(link) for link in _annotation_links(session)]
```

(`shared_layers()` лишається без змін.) Після `shared_layers()`:

```python
def shared_annotations(user, session=None):
    """Чужі нотатки для читання — єдине місце, звідки їх беруть читалка, API й синк.

    Ті самі джерела, що в shared_layers(), але на рівні нотатки: посилання на книгу
    з обраними темами відкриває лише нотатки цих тем. Джерела об'єднуються через OR,
    тож публікація для групи (весь шар) перекриває обмеження посилання.
    """
    authenticated = user is not None and user.is_authenticated
    sources = []
    for link in _annotation_links(session):
        scope = _link_layer_scope(link, prefix="layer__")
        if link.book_id and link.annotation_scope == ShareLink.AnnotationScope.SELECTED:
            # Порожній набір (теми видалено) — жодної нотатки, а не весь шар.
            scope &= Q(group_id__in=list(link.note_groups.values_list("pk", flat=True)))
        sources.append(scope)
    if authenticated:
        sources.append(
            Q(
                layer__publications__group__memberships__user=user,
                layer__publications__group__owner=F("layer__owner"),
            )
        )
    if not sources:
        return Annotation.objects.none()

    annotations = Annotation.objects.filter(
        reduce(or_, sources),
        layer__book__in=visible_books(user, session).values("pk"),
    )
    if authenticated:
        annotations = annotations.exclude(layer__owner=user)
    return annotations.distinct()
```

- [ ] **Step 5: serialize_shared_layers**

У `PDF+/annotations/serializers.py` додай `from collections import defaultdict` на початку і заміни `serialize_shared_layers`:

```python
def serialize_shared_layers(layers, annotations):
    """Чужі шари з нотатками, які читач має право бачити (див. access.shared_annotations).

    Поле group не віддаємо: отримувач бачить нотатки без тем автора.
    """
    by_layer = defaultdict(list)
    for annotation in annotations.order_by("page_number", "created_at"):
        item = serialize_annotation(annotation)
        del item["group"]
        by_layer[annotation.layer_id].append(item)
    return [
        {"id": layer.pk, "owner": layer.owner.username, "annotations": by_layer[layer.pk]}
        for layer in layers.select_related("owner").order_by("owner__username")
    ]
```

- [ ] **Step 6: Callers**

`PDF+/annotations/api.py`: імпорт `from .access import shared_annotations, shared_layers`; тіло `book_shared_layers`:

```python
    book = get_object_or_404(visible_books(request.user, request.session), pk=book_pk)
    layers = shared_layers(request.user, request.session).filter(book=book)
    annotations = shared_annotations(request.user, request.session).filter(layer__book=book)
    return JsonResponse({"layers": serialize_shared_layers(layers, annotations)})
```

`PDF+/book_library/views.py`: імпорт `from annotations.access import publishable_groups, shared_annotations, shared_layers`.

У `book_reader_view` заміни `"shared_layers_data": ...`:

```python
            "shared_layers_data": serialize_shared_layers(
                shared_layers(request.user, request.session).filter(book=book),
                shared_annotations(request.user, request.session).filter(layer__book=book),
            ),
```

У `book_detail_view` заміни блок `teacher_layers = (...)` (разом із коментарем над ним):

```python
    # Лічильник — лише з нотаток, які читач бачить (посилання може відкривати не всі теми).
    # order_by() скидає Meta.ordering Annotation: інакше page_number потрапив би в GROUP BY.
    shared = shared_annotations(request.user, request.session).filter(layer__book=book)
    shared_counts = dict(
        Annotation.objects.filter(pk__in=shared.values("pk"))
        .order_by()
        .values("layer_id")
        .annotate(count=Count("pk"))
        .values_list("layer_id", "count")
    )
    teacher_layers = list(
        Layer.objects.filter(
            pk__in=shared_layers(request.user, request.session).filter(book=book).values("pk")
        )
        .select_related("owner")
        .order_by("owner__username")
    )
    for teacher_layer in teacher_layers:
        teacher_layer.annotation_count = shared_counts.get(teacher_layer.pk, 0)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python manage.py test annotations.tests.ShareLinkTopicTests annotations.tests.ShareLinkLayerTests annotations.tests.SharedLayerApiTests annotations.tests.SharedLayerPageTests`
Expected: PASS.

- [ ] **Step 8: Checkpoint**

Run: `uv run python manage.py test annotations book_library sharing api && uv run python manage.py makemigrations --check --dry-run`
Expected: усе зелене; `No changes detected`.

---

### Task 4: Вибір тем у формі посилання

**Files:**
- Modify: `PDF+/sharing/forms.py`
- Modify: `PDF+/sharing/views.py` (`link_create_view`, `link_list_view`)
- Modify: `PDF+/sharing/templates/sharing/link_form.html`
- Modify: `PDF+/sharing/templates/sharing/link_list.html`
- Test: `PDF+/sharing/tests.py` (клас `ShareLinkTopicFormTests`)

**Interfaces:**
- Consumes: Task 3 — `ShareLink.AnnotationScope`, `ShareLink.note_groups`; Task 1 — `NoteGroup`.
- Produces: поле форми `note_groups` (POST-ключ `note_groups`, значення — id тем).

- [ ] **Step 1: Write the failing tests**

У `PDF+/sharing/tests.py` додай імпорт `from annotations.models import Layer, NoteGroup` і в кінець:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test sharing.tests.ShareLinkTopicFormTests`
Expected: FAIL — посилання створюється зі `scope="all"` / нема тексту помилки.

- [ ] **Step 3: Form**

У `PDF+/sharing/forms.py` додай імпорт `from annotations.models import NoteGroup` і перед `class ShareLinkForm`:

```python
class NoteGroupCheckboxes(forms.CheckboxSelectMultiple):
    """Чекбокси тем із data-book: скрипт форми лишає видимими теми обраної книги."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-book"] = instance.layer.book_id
        return option
```

У `ShareLinkForm.Meta`:

```python
        fields = ("book", "folder", "include_annotations", "note_groups", "expires_at")
```

у `labels` — `"note_groups": "Які теми показати",`; у `widgets` — `"note_groups": NoteGroupCheckboxes,`; у `help_texts` — `"note_groups": "Нічого не обрано — усі нотатки книги. Лише для посилання на книгу.",`.

У `__init__` після налаштування `folder` (перед `self.helper = FormHelper()`):

```python
        groups = (
            NoteGroup.objects.none()
            if owner is None
            else NoteGroup.objects.filter(layer__owner=owner)
            .select_related("layer__book")
            .order_by("layer__book__title", "name")
        )
        if groups.exists():
            self.fields["note_groups"].queryset = groups
            self.fields["note_groups"].label_from_instance = (
                lambda group: f"{group.layer.book.title} — {group.name}"
            )
        else:
            del self.fields["note_groups"]
```

У `Layout(...)` заміни рядок `"include_annotations",` на:

```python
            "include_annotations",
            *(["note_groups"] if "note_groups" in self.fields else []),
```

Наприкінці `clean()` (перед `return cleaned`):

```python
        groups = cleaned.get("note_groups")
        if groups:
            if not book or not cleaned.get("include_annotations"):
                raise forms.ValidationError(
                    "Теми можна обрати лише для посилання на книгу з нотатками."
                )
            if any(group.layer.book_id != book.pk for group in groups):
                self.add_error("note_groups", "Обрані теми мають належати обраній книзі.")
```

Новий метод у `ShareLinkForm`:

```python
    def save(self, commit=True):
        link = super().save(commit=False)
        link.annotation_scope = (
            ShareLink.AnnotationScope.SELECTED
            if self.cleaned_data.get("note_groups")
            else ShareLink.AnnotationScope.ALL
        )
        if commit:
            link.save()
            self.save_m2m()
        return link
```

(Чужа тема в POST відсіюється самим `ModelMultipleChoiceField`: її нема в queryset — «Оберіть коректний варіант».)

- [ ] **Step 4: Views**

`PDF+/sharing/views.py`, `link_create_view` — після `link.save()`:

```python
        form.save_m2m()
```

`link_list_view` — `"links"`:

```python
            "links": ShareLink.objects.filter(created_by=request.user)
            .select_related("book", "folder")
            .prefetch_related("note_groups"),
```

- [ ] **Step 5: Templates**

`PDF+/sharing/templates/sharing/link_list.html` — після блоку `{% if link.include_annotations %}...{% endif %}` у першій комірці:

```django
                {% if link.annotation_scope == "selected" %}
                  <span class="badge text-bg-light border ms-1" title="Отримувач бачить лише ці теми">
                    <i class="bi bi-folder2-open me-1"></i>{% for group in link.note_groups.all %}{{ group.name }}{% if not forloop.last %}, {% endif %}{% empty %}теми видалено{% endfor %}
                  </span>
                {% endif %}
```

(`NoteGroup.Meta.ordering = ["name", "pk"]`, тож «Відповіді, Формули» — за абеткою.)

`PDF+/sharing/templates/sharing/link_form.html` — у кінець:

```django
{% block extra_js %}
<script>
  // Лишаємо видимими лише теми обраної книги; теми інших книг знімаємо.
  (() => {
    const book = document.querySelector('select[name="book"]');
    const boxes = document.querySelectorAll('input[name="note_groups"]');
    if (!book || !boxes.length) return;
    const sync = () => {
      for (const box of boxes) {
        const same = box.dataset.book === book.value;
        box.closest('.form-check')?.classList.toggle('d-none', !same);
        if (!same) box.checked = false;
      }
    };
    book.addEventListener('change', sync);
    sync();
  })();
</script>
{% endblock %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python manage.py test sharing`
Expected: PASS (нові + наявні `ShareLinkTests`).

- [ ] **Step 7: Checkpoint**

Run: `uv run python manage.py test sharing annotations`
Expected: усе зелене.

---

### Task 5: Сторінка «Мої нотатки» за темами

**Files:**
- Modify: `PDF+/annotations/views.py`
- Modify: `PDF+/annotations/urls.py`
- Modify: `PDF+/annotations/templates/annotations/annotation_list.html`
- Test: `PDF+/annotations/tests.py` (клас `NoteGroupPageTests`)

**Interfaces:**
- Consumes: Task 1 — `rename_note_group`, `delete_note_group`, `parse_group_name`.
- Produces: URL names `annotations:note_group_rename`, `annotations:note_group_delete`; контекст `sections` (`[{"group": NoteGroup | None, "count": int, "pages": [(page, [Annotation])]}]`), `show_group_headings: bool`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test annotations.tests.NoteGroupPageTests`
Expected: FAIL — `KeyError: 'sections'` / `NoReverseMatch`.

- [ ] **Step 3: Views**

У `PDF+/annotations/views.py` додай імпорти:

```python
from .groups import delete_note_group, rename_note_group
from .models import Annotation, Layer, NoteGroup
from .validators import PayloadError, parse_group_name
```

(заміни наявний `from .models import Annotation, Layer`). Заміни тіло `book_annotations_view` від `annotations = list(...)` до `return render(...)` включно:

```python
    annotations = list(
        Annotation.objects.filter(layer=layer).select_related("layer")
        if layer
        else Annotation.objects.none()
    )
    groups = list(layer.note_groups.all()) if layer else []

    # Теми за назвою (порожні теж — щоб їх можна було перейменувати чи видалити),
    # «Без групи» останньою і лише коли в ній щось є.
    sections = []
    for group in [*groups, None]:
        group_id = group.pk if group else None
        items = [a for a in annotations if a.group_id == group_id]
        if group is None and not items:
            continue
        sections.append(
            {
                "group": group,
                "count": len(items),
                "pages": [
                    (page_number, list(rows))
                    for page_number, rows in groupby(items, key=lambda a: a.page_number)
                ],
            }
        )

    return render(
        request,
        "annotations/annotation_list.html",
        {
            "book": book,
            "sections": sections,
            "show_group_headings": bool(groups),
            "total_count": len(annotations),
            "pages_with_notes": len({a.page_number for a in annotations}),
            "with_notes_count": sum(1 for a in annotations if a.note_text),
            "reader_url": reverse("book_library:book_reader", args=[book.pk]),
            "crumbs": [
                {"url": reverse("book_library:library"), "label": "Бібліотека"},
                {"url": reverse("book_library:book_detail", args=[book.pk]), "label": book.title},
                {"url": "", "label": "Нотатки"},
            ],
            "page_title": "Мої нотатки",
            "page_subtitle": book.title,
        },
    )
```

У кінець файлу:

```python
def _own_group(request, pk):
    return get_object_or_404(NoteGroup.objects.select_related("layer"), pk=pk, layer__owner=request.user)


@login_required
@require_POST
def note_group_rename_view(request, pk):
    group = _own_group(request, pk)
    try:
        rename_note_group(group, parse_group_name(request.POST.get("name")))
    except PayloadError as error:
        messages.error(request, f"Не вдалося перейменувати тему: {error}")
    else:
        messages.success(request, "Тему перейменовано.")
    return redirect("annotations:book_annotations", book_pk=group.layer.book_id)


@login_required
@require_POST
def note_group_delete_view(request, pk):
    group = _own_group(request, pk)
    book_pk = group.layer.book_id
    delete_note_group(group)
    messages.success(request, "Тему видалено, її нотатки тепер «Без групи».")
    return redirect("annotations:book_annotations", book_pk=book_pk)
```

- [ ] **Step 4: URLs**

`PDF+/annotations/urls.py` після `annotation_delete`:

```python
    path("note-groups/<int:pk>/rename/", views.note_group_rename_view, name="note_group_rename"),
    path("note-groups/<int:pk>/delete/", views.note_group_delete_view, name="note_group_delete"),
```

- [ ] **Step 5: Template**

У `PDF+/annotations/templates/annotations/annotation_list.html` заміни вміст `<div class="col-lg-8">...</div>`:

```django
  <div class="col-lg-8">
    {% for section in sections %}
      {% if show_group_headings %}
        <div class="d-flex flex-wrap align-items-center gap-2 mt-4 mb-2">
          <h2 class="h5 mb-0">
            <i class="bi {% if section.group %}bi-folder2-open{% else %}bi-folder2{% endif %} me-1"></i>
            {% if section.group %}{{ section.group.name }}{% else %}Без групи{% endif %}
          </h2>
          <span class="badge text-bg-light border">{{ section.count }}</span>
          {% if section.group %}
            <form method="post" action="{% url 'annotations:note_group_rename' section.group.pk %}"
                  class="d-flex gap-1 ms-auto">
              {% csrf_token %}
              <input type="text" name="name" value="{{ section.group.name }}" maxlength="100" required
                     class="form-control form-control-sm" aria-label="Нова назва теми">
              <button type="submit" class="btn btn-sm btn-outline-secondary" title="Перейменувати">
                <i class="bi bi-pencil"></i>
              </button>
            </form>
            <button type="button" class="btn btn-sm btn-outline-danger" title="Видалити тему"
                    data-bs-toggle="modal" data-bs-target="#confirmModal"
                    data-action="{% url 'annotations:note_group_delete' section.group.pk %}"
                    data-title="Видалити тему «{{ section.group.name }}»?"
                    data-text="Нотатки не зникнуть — вони перейдуть у «Без групи»."
                    data-confirm="Видалити тему">
              <i class="bi bi-trash"></i>
            </button>
          {% endif %}
        </div>
      {% endif %}

      {% for page_number, page_annotations in section.pages %}
        <h3 class="h6 text-body-secondary text-uppercase mb-2 mt-3">Сторінка {{ page_number }}</h3>
        {% for annotation in page_annotations %}
          {% include "annotations/partials/annotation_item.html" %}
        {% endfor %}
      {% empty %}
        <p class="small text-body-secondary mb-0">У темі поки нема нотаток.</p>
      {% endfor %}
    {% empty %}
      {% include "partials/empty_state.html" with icon="bi-highlighter" title="Нотаток немає" text="Виділення, зроблені в читалці, з'являться тут." action_url=reader_url action_label="Відкрити книгу" %}
    {% endfor %}
  </div>
```

(Якщо в шарі нема ні нотаток, ні тем — `sections` порожній → empty state, як раніше.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python manage.py test annotations.tests.NoteGroupPageTests annotations.tests.AnnotationPageTests`
Expected: PASS (наявний `test_list_groups_by_page` шукає «Сторінка 7» — він лишається в `<h3>`).

- [ ] **Step 7: Checkpoint**

Run: `uv run python manage.py test annotations`
Expected: усе зелене.

---

### Task 6: Читалка — перемикач тем і тема в поповері

**Files:**
- Modify: `PDF+/book_library/views.py` (`book_reader_view`)
- Modify: `PDF+/book_library/templates/book_library/book_reader.html`
- Modify: `PDF+/static/js/reader.js`
- Test: `PDF+/annotations/tests.py` (клас `ReaderTopicTests`)

**Interfaces:**
- Consumes: Task 2 — `serialize_note_group`, URL names `annotations:api_book_note_groups`, `annotations:api_note_group_detail`; анотації в `annotations_data` з ключем `group`.
- Produces: `#topicSwitch` (`[data-topic-select]`, `[data-topic-form]`, `[data-topic-name]`, `[data-topic-cancel]`, `[data-topic-error]`, `[data-topic-hint]`), `[data-popover-topic]`, `#note-groups-data`; `data-note-groups-url`, `data-book-id` на `#pdf-viewer`; ключ `localStorage` `pdfplus:topic:<book_id>`.

- [ ] **Step 1: Write the failing tests**

```python
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
        self.assertContains(response, "data-popover-topic")
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test annotations.tests.ReaderTopicTests`
Expected: FAIL — `KeyError: 'note_groups_data'`.

- [ ] **Step 3: View context**

`PDF+/book_library/views.py`: імпорт `from annotations.serializers import serialize_annotation, serialize_note_group, serialize_shared_layers`. У `book_reader_view` після `annotations = (...)`:

```python
    note_groups = list(layer.note_groups.all()) if layer else []
```

У контекст додай:

```python
            "note_groups_data": [serialize_note_group(group) for group in note_groups],
```

- [ ] **Step 4: Template**

`PDF+/book_library/templates/book_library/book_reader.html`:

a) У `#pdf-viewer`, всередині `{% if user.is_authenticated %}` після `data-progress-url=...`:

```django
           data-book-id="{{ book.pk }}"
           data-note-groups-url="{% url 'annotations:api_book_note_groups' book.pk %}"
```

b) Після закриття блоку `{% if shared_layers_data %}...{% endif %}` (перед `<div class="px-3 py-2 border-bottom d-none" id="annotationFilter">`):

```django
      {% if user.is_authenticated %}
        <div class="px-3 py-2 border-bottom" id="topicSwitch" data-own-only>
          <div class="d-flex align-items-center gap-2">
            <label class="small text-body-secondary mb-0" for="topicSelect">Тема</label>
            <select class="form-select form-select-sm" id="topicSelect" data-topic-select>
              <option value="all">Усі теми</option>
              <option value="none">Без групи</option>
              {% for group in note_groups_data %}
                <option value="{{ group.id }}">{{ group.name }}</option>
              {% endfor %}
              <option value="new">+ Нова тема…</option>
            </select>
          </div>
          <form class="mt-2 d-none" data-topic-form>
            <div class="input-group input-group-sm">
              <input type="text" class="form-control" maxlength="100" required
                     placeholder="Назва теми" aria-label="Назва нової теми" data-topic-name>
              <button type="submit" class="btn btn-primary">Створити</button>
              <button type="button" class="btn btn-outline-secondary" data-topic-cancel>
                Скасувати
              </button>
            </div>
            <div class="small text-danger mt-1" data-topic-error></div>
          </form>
          <p class="small text-body-secondary mb-0 mt-1" data-topic-hint></p>
        </div>
      {% endif %}
```

c) У поповері `#annotationPopover` перед `<textarea ... data-note ...>`:

```django
      <select class="form-select form-select-sm mb-2" data-popover-topic aria-label="Тема нотатки">
        <option value="none">Без групи</option>
        {% for group in note_groups_data %}
          <option value="{{ group.id }}">{{ group.name }}</option>
        {% endfor %}
        <option value="new">+ Нова тема…</option>
      </select>
```

d) Після `{{ shared_layers_data|json_script:"shared-layers-data" }}`:

```django
{{ note_groups_data|json_script:"note-groups-data" }}
```

- [ ] **Step 5: Run Python tests to verify they pass**

Run: `uv run python manage.py test annotations.tests.ReaderTopicTests annotations.tests.AnnotationPageTests`
Expected: PASS.

- [ ] **Step 6: reader.js — стан і фільтр**

`PDF+/static/js/reader.js`:

a) Після `const layerTitle = document.querySelector("[data-layer-title]");`:

```js
  const topicSwitch = document.getElementById("topicSwitch");
  const topicSelect = topicSwitch?.querySelector("[data-topic-select]");
  const topicForm = topicSwitch?.querySelector("[data-topic-form]");
  const topicHint = topicSwitch?.querySelector("[data-topic-hint]");
  const topicError = topicSwitch?.querySelector("[data-topic-error]");
  const popoverTopic = popover?.querySelector("[data-popover-topic]");
  const TOPIC_STORAGE_KEY = `pdfplus:topic:${viewer.dataset.bookId}`;
```

b) Після `const chipNodes = new Map();`:

```js
  // Теми власного шару. activeTopic — "all" | "none" | id теми рядком:
  // і куди йдуть нові виділення, і що показується.
  const noteGroups = new Map();
  let activeTopic = "all";
```

c) Після блоку читання `shared-layers-data` (перед `// Шар, що зараз на сторінках...`):

```js
  const groupsNode = document.getElementById("note-groups-data");
  if (groupsNode) {
    for (const group of JSON.parse(groupsNode.textContent)) {
      noteGroups.set(group.id, group);
    }
  }
```

d) Заміни рядок `const isVisible = (annotation) => !hiddenColors.has(annotation.color);` на:

```js
  // Теми — лише у власному шарі; на чужих шарах фільтр тем не діє.
  function matchesTopic(annotation) {
    if (!ownShown() || activeTopic === "all") {
      return true;
    }
    if (activeTopic === "none") {
      return annotation.group === null;
    }
    return annotation.group === Number(activeTopic);
  }

  const isVisible = (annotation) =>
    !hiddenColors.has(annotation.color) && matchesTopic(annotation);

  const topicScoped = () => [...shown.values()].filter(matchesTopic);
```

e) У `renderSidebar()` заміни блок лічильника:

```js
    const total = topicScoped().length;
    if (counter) {
      counter.textContent = hiddenColors.size ? `${sorted.length}/${total}` : String(total);
    }
```

і порожній стан:

```js
    if (!sorted.length) {
      const topicFiltered = ownShown() && activeTopic !== "all";
      if (sidebar.dataset.jsOwned !== "1" && !hiddenColors.size && !topicFiltered) {
        return;
      }

      sidebar.dataset.jsOwned = "1";
      const empty = document.createElement("p");
      empty.className = "text-body-secondary small text-center py-4 mb-0";
      if (hiddenColors.size) {
        empty.textContent = "Усі мітки сховані фільтром.";
      } else if (topicFiltered) {
        empty.textContent = "У цій темі нотаток ще немає.";
      } else {
        empty.textContent = ownShown() ? "Нотаток ще немає." : "У цьому шарі нотаток немає.";
      }
      sidebar.replaceChildren(empty);
      return;
    }
```

f) У `colorCounts()` заміни `for (const annotation of shown.values())` на `for (const annotation of topicScoped())`.

g) У `renderFilters()` заміни блок `filterSummary`:

```js
    if (filterSummary) {
      const total = topicScoped().length;
      filterSummary.textContent = hiddenColors.size
        ? `${visibleSorted().length} з ${total}`
        : `${total} міток`;
    }
```

- [ ] **Step 7: reader.js — функції тем**

Після функції `refresh()`:

```js
  function sortedGroups() {
    return [...noteGroups.values()].sort((a, b) => a.name.localeCompare(b.name, "uk"));
  }

  function fillTopicOptions(select, { withAll }) {
    if (!select) {
      return;
    }
    const options = [];
    const add = (value, label) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      options.push(option);
    };
    if (withAll) {
      add("all", "Усі теми");
    }
    add("none", "Без групи");
    for (const group of sortedGroups()) {
      add(String(group.id), group.name);
    }
    add("new", "+ Нова тема…");
    select.replaceChildren(...options);
  }

  function renderTopics() {
    fillTopicOptions(topicSelect, { withAll: true });
    fillTopicOptions(popoverTopic, { withAll: false });
    if (topicSelect) {
      topicSelect.value = activeTopic;
    }
    if (topicHint) {
      const group = noteGroups.get(Number(activeTopic));
      topicHint.textContent = group
        ? `Нові виділення потрапляють у тему «${group.name}».`
        : "Нові виділення потрапляють у «Без групи».";
    }
  }

  function setTopic(key) {
    activeTopic = key === "none" || noteGroups.has(Number(key)) ? String(key) : "all";
    try {
      localStorage.setItem(TOPIC_STORAGE_KEY, activeTopic);
    } catch {
      // Сховище недоступне (приватний режим) — тема просто не запам'ятається.
    }
    navId = null;
    renderTopics();
    refresh();
  }

  const newHighlightGroup = () =>
    noteGroups.has(Number(activeTopic)) ? Number(activeTopic) : null;

  async function createTopic(name) {
    const group = await request(viewer.dataset.noteGroupsUrl, "POST", { name });
    noteGroups.set(group.id, group);
    return group;
  }
```

- [ ] **Step 8: reader.js — створення, поповер, ініціалізація**

a) У `createHighlight` заміни тіло запиту:

```js
      const created = await request(viewer.dataset.annotationsUrl, "POST", {
        ...payload,
        color,
        group: newHighlightGroup(),
      });
```

b) В `openPopover` після рядка з `[data-status]`:

```js
    if (popoverTopic) {
      popoverTopic.value = annotation.group === null ? "none" : String(annotation.group);
    }
```

c) Усередині `if (canAnnotate) {`, після обробника `popover?.querySelector("[data-note]")?.addEventListener("blur", ...)`:

```js
    topicSelect?.addEventListener("change", () => {
      if (topicSelect.value === "new") {
        topicSelect.value = activeTopic;
        topicForm?.classList.remove("d-none");
        topicForm?.querySelector("[data-topic-name]")?.focus();
        return;
      }
      setTopic(topicSelect.value);
    });

    topicForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = topicForm.querySelector("[data-topic-name]");
      try {
        const group = await createTopic(input.value);
        input.value = "";
        topicError.textContent = "";
        topicForm.classList.add("d-none");
        setTopic(String(group.id));
      } catch (error) {
        topicError.textContent = error.message;
      }
    });

    topicForm?.querySelector("[data-topic-cancel]")?.addEventListener("click", () => {
      topicForm.classList.add("d-none");
      topicError.textContent = "";
    });

    popoverTopic?.addEventListener("change", async () => {
      const annotation = annotations.get(activeId);
      if (!annotation) {
        return;
      }
      let value = popoverTopic.value;
      if (value === "new") {
        const name = window.prompt("Назва нової теми");
        if (!name) {
          popoverTopic.value = annotation.group === null ? "none" : String(annotation.group);
          return;
        }
        try {
          value = String((await createTopic(name)).id);
        } catch (error) {
          popover.querySelector("[data-status]").textContent = error.message;
          popoverTopic.value = annotation.group === null ? "none" : String(annotation.group);
          return;
        }
        renderTopics();
        popoverTopic.value = value;
      }
      // Нотатка, що вийшла з показаної теми, зникає з панелі, а поповер закривається в refresh().
      patchActive({ group: value === "none" ? null : Number(value) }, "Тему змінено");
    });
```

d) Перед рядком `try {` наприкінці файлу (стартове завантаження PDF):

```js
  let storedTopic = null;
  try {
    storedTopic = localStorage.getItem(TOPIC_STORAGE_KEY);
  } catch {
    // Сховище недоступне — стартуємо з «Усі теми».
  }
  if (storedTopic === "none" || noteGroups.has(Number(storedTopic))) {
    activeTopic = storedTopic;
  }
  renderTopics();
```

e) У стартовому блоці після `const lastPage = Number(viewer.dataset.lastPage);`:

```js
    // ?annotation= на власну нотатку з іншої теми: показуємо всі теми, інакше мітки не буде.
    const ownTarget = annotations.get(targetId);
    if (ownTarget && !matchesTopic(ownTarget)) {
      activeTopic = "all";
      renderTopics();
    }
```

- [ ] **Step 9: Manual verification (Playwright)**

Сервер: `uv run python manage.py runserver 127.0.0.1:8000` (у фоні). Вхід: `admin` / пароль з пам'яті проєкту; шлях адмінки не потрібен. Книга з текстовим шаром (напр. pk=20).

1. Відкрити `/books/20/read/` → у панелі є «Тема: Усі теми».
2. Обрати «+ Нова тема…» → ввести «Еволюція» → «Створити»: тема в списку, активна, підказка «Нові виділення потрапляють у тему «Еволюція»», панель порожня з текстом «У цій темі нотаток ще немає.».
3. Виділити текст → обрати колір: нотатка з'являється в панелі; `GET /annotations/api/books/20/annotations/` показує `"group": <id>`.
4. Перемкнути «Без групи» → нової нотатки не видно, старі видно; мітки на сторінці теж фільтруються.
5. Клік по старій мітці → у поповері селект «Тема» → «Еволюція»: статус «Тему змінено», нотатка зникає з панелі «Без групи».
6. Перезавантажити сторінку → тема «Без групи» збереглась.
7. **Review Focus 1:** при активній «Без групи» відкрити `/books/20/read/?annotation=<id нотатки з «Еволюції»>` → тема скинута на «Усі теми», сторінка прокручена до мітки.
8. Консоль браузера без помилок.

Expected: усі 8 пунктів відповідають опису; скріншоти — у `.playwright-mcp/`.

- [ ] **Step 10: Checkpoint**

Run: `uv run python manage.py test annotations book_library`
Expected: усе зелене.

---

### Task 7: DRF-синк — теми в pull і push

**Files:**
- Modify: `PDF+/api/serializers.py`
- Modify: `PDF+/api/sync.py`
- Test: `PDF+/api/tests.py` (клас `NoteGroupSyncTests`)

**Interfaces:**
- Consumes: Task 1 — `create_note_group`, `rename_note_group`, `delete_note_group`, `group_fields`, `parse_group_name`, `NoteGroup`; Task 3 — `shared_annotations`.
- Produces: у pull — ключ `note_groups` (`[{id, book, name, created_at, updated_at}]`), `annotations[].group`; у push — секція `note_groups` {create, update, delete}, у результаті `note_groups` {created, updated, deleted}, помилки з `op` `group_create` / `group_update` / `group_delete`; анотації приймають `group` або `group_client_id`.

- [ ] **Step 1: Write the failing tests**

У `PDF+/api/tests.py` імпорти: `from unittest.mock import patch`, `from annotations.groups import create_note_group`, `from annotations.models import Annotation, Layer, LayerPublication, NoteGroup`. У кінець:

```python
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
```

(`foreign.pk` у `delete` чужої теми мовчки ігнорується — так само, як видалення чужої анотації в наявному коді.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python manage.py test api.tests.NoteGroupSyncTests`
Expected: FAIL — `KeyError: 'note_groups'`.

- [ ] **Step 3: Serializers**

`PDF+/api/serializers.py` — імпорт `NoteGroup` поряд з `Annotation`, `Layer`. В `AnnotationSerializer.Meta.fields` додай `"group",` після `"color",`. Заміни `SharedAnnotationSerializer`:

```python
class SharedAnnotationSerializer(AnnotationSerializer):
    # Без group: учні бачать нотатки вчителя без його тем.
    class Meta(AnnotationSerializer.Meta):
        fields = [f for f in AnnotationSerializer.Meta.fields if f != "group"] + ["layer"]
```

Після `AnnotationSerializer`:

```python
class NoteGroupSerializer(serializers.ModelSerializer):
    book = serializers.IntegerField(source="layer.book_id", read_only=True)

    class Meta:
        model = NoteGroup
        fields = ["id", "book", "name", "created_at", "updated_at"]
```

- [ ] **Step 4: sync.py — pull**

Імпорти в `PDF+/api/sync.py`:

```python
from annotations.access import shared_annotations as readable_shared_annotations
from annotations.access import shared_layers
from annotations.groups import create_note_group, delete_note_group, group_fields, rename_note_group
from annotations.models import Annotation, Layer, LayerPublication, NoteGroup
from annotations.validators import (
    PayloadError,
    parse_annotation_update,
    parse_group_name,
    parse_new_annotation,
    parse_page_number,
)
```

і `NoteGroupSerializer` у імпорт з `.serializers`.

У `pull_state` заміни рядок `shared_annotations = Annotation.objects.filter(layer_id__in=layer_ids)...`:

```python
    # Без сесії посилання не діють — лишаються публікації для груп, тобто шари цілком.
    shared_annotations = readable_shared_annotations(user).select_related("layer")
    note_groups = NoteGroup.objects.filter(layer__owner=user).select_related("layer")
```

У словник результату після `"deleted_annotation_ids": deleted_ids,`:

```python
        # Тем мало, тож список щоразу повний: тема, якої нема, — видалена (без тумбстоунів).
        "note_groups": NoteGroupSerializer(note_groups, many=True).data,
```

- [ ] **Step 5: sync.py — push**

Заміни `_ops`:

```python
def _ops(section, name, prefix="annotations"):
    raw = section.get(name, [])
    if not isinstance(raw, list):
        raise BatchError(f"{prefix}.{name}: очікується список")
    return raw
```

Додай перед `_create`:

```python
def _visible_book(user, item):
    book_id = _int_id(item.get("book"), "book")
    try:
        return visible_books(user).get(pk=book_id)
    except Book.DoesNotExist:
        raise PayloadError("book: книги нема або вона недоступна")


def _create_group(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    book = _visible_book(user, item)
    return create_note_group(book, user, parse_group_name(item.get("name")))


def _update_group(user, item):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    pk = _int_id(item.get("id"), "id")
    group = NoteGroup.objects.filter(pk=pk, layer__owner=user).first()
    if group is None:
        raise PayloadError("id: теми нема або вона не ваша")
    return rename_note_group(group, parse_group_name(item.get("name")))
```

Заміни `_create` і `_update`:

```python
def _create(user, item, client_groups):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    book = _visible_book(user, item)
    fields = parse_new_annotation(item, book.page_count)
    fields.update(group_fields(item, book.pk, user, client_groups))
    layer, _ = Layer.objects.get_or_create(book=book, owner=user)
    return Annotation.objects.create(layer=layer, **fields)


def _update(user, item, client_groups):
    if not isinstance(item, dict):
        raise PayloadError("очікується JSON-об'єкт")
    pk = _int_id(item.get("id"), "id")
    annotation = Annotation.objects.filter(pk=pk, layer__owner=user).select_related("layer").first()
    if annotation is None:
        raise PayloadError("id: анотації нема або вона не ваша")

    changes = {k: v for k, v in item.items() if k not in ("id", "group_client_id")}
    fields = parse_annotation_update(changes) if changes else {}
    fields.update(group_fields(item, annotation.layer.book_id, user, client_groups))
    if not fields:
        raise PayloadError("нема що оновлювати")
    for name, value in fields.items():
        setattr(annotation, name, value)
    annotation.save(update_fields=[*fields, "updated_at"])
    return annotation
```

У `_record_progress` заміни пошук книги на `book = _visible_book(user, item)` (рядки `book_id = ...` і `try/except` прибрати).

У `push_changes` після перевірки `section`:

```python
    group_section = payload.get("note_groups", {})
    if not isinstance(group_section, dict):
        raise BatchError("note_groups: очікується об'єкт із create/update/delete")
    group_creates = _ops(group_section, "create", "note_groups")
    group_updates = _ops(group_section, "update", "note_groups")
    group_deletes = _ops(group_section, "delete", "note_groups")
```

Заміни перевірку ліміту:

```python
    total = (
        len(creates) + len(updates) + len(deletes) + len(progress_items)
        + len(group_creates) + len(group_updates) + len(group_deletes)
    )
    if total > MAX_BATCH_OPS:
        raise BatchError(f"забагато операцій (максимум {MAX_BATCH_OPS})")

    result = {
        "created": [], "updated": [], "deleted": [], "reading_progress": [],
        "note_groups": {"created": [], "updated": [], "deleted": []},
        "errors": [],
    }

    # Теми — до анотацій: нотатка може посилатися на тему з цього ж пушу (group_client_id).
    client_groups = {}
    for item in group_creates:
        client_id = item.get("client_id") if isinstance(item, dict) else None
        try:
            group = _create_group(user, item)
        except PayloadError as error:
            result["errors"].append({"op": "group_create", "client_id": client_id, "error": str(error)})
            continue
        if isinstance(client_id, (str, int)):
            client_groups[client_id] = group
        result["note_groups"]["created"].append({"client_id": client_id, **NoteGroupSerializer(group).data})

    for item in group_updates:
        pk = item.get("id") if isinstance(item, dict) else None
        try:
            group = _update_group(user, item)
        except PayloadError as error:
            result["errors"].append({"op": "group_update", "id": pk, "error": str(error)})
            continue
        result["note_groups"]["updated"].append(NoteGroupSerializer(group).data)
```

У циклах анотацій: `_create(user, item)` → `_create(user, item, client_groups)`, `_update(user, item)` → `_update(user, item, client_groups)`.

Після циклу `for pk in deletes:` (анотацій) і перед `for item in progress_items:`:

```python
    # Видалення тем — після анотацій: зміни нотаток цього ж пушу не впадуть на зниклу тему.
    for pk in group_deletes:
        try:
            _int_id(pk, "delete")
        except PayloadError as error:
            result["errors"].append({"op": "group_delete", "id": pk, "error": str(error)})
            continue
        group = NoteGroup.objects.filter(pk=pk, layer__owner=user).first()
        if group is not None:
            delete_note_group(group)
        result["note_groups"]["deleted"].append(pk)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python manage.py test api`
Expected: PASS (нові + наявні `SyncPushTests`, `SharedLayerSyncTests`, `ProgressSyncTests`). Якщо наявний тест порівнює `data["shared_annotations"][0]` чи `data["annotations"][0]` цілим словником — додай/прибери `group` відповідно до нових серіалізаторів.

- [ ] **Step 7: Checkpoint**

Run: `uv run python manage.py test api annotations`
Expected: усе зелене.

---

### Task 8: Документація і фінальна перевірка

**Files:**
- Modify: `CLAUDE.md` (розділ «Анотації»)
- Modify: `README.md`

- [ ] **Step 1: CLAUDE.md**

У розділі `### Анотації` після абзацу про `annotations/validators.py` додай:

```markdown
Теми нотаток — `NoteGroup` (FK на `Layer`, унікальна назва в межах шару);
`Annotation.group` — `SET_NULL`, `null` = «Без групи». Операції з темами —
`annotations/groups.py`, спільний для вебу й синку: `group_fields()` розв'язує `group` /
`group_client_id` з payload (тема лише з шару цього власника на цій книзі), а
**видаляти тему тільки через `delete_note_group()`** — голий `delete()` через `SET_NULL`
не оновить `updated_at`, і дельта синку не побачить звільнених нотаток.
```

Заміни речення «Чужі шари читай лише через `annotations/access.shared_layers()`: …» на початок:

```markdown
Чужі шари перелічує `annotations/access.shared_layers()`, а чужі **нотатки** —
лише `shared_annotations()`: share-посилання на книгу з `annotation_scope="selected"`
відкриває тільки обрані теми (`ShareLink.note_groups`; порожній набір — нічого, не весь
шар). Джерела: членство в групі, …
```

(решту речення — без змін).

- [ ] **Step 2: README.md**

Після рядка «- Нотатка до кожного виділення. Бокова панель зі списком анотацій і фільтр за кольором.» додай:

```markdown
- Теми нотаток у межах книги: перемикач «Тема» в читалці визначає, куди йдуть нові виділення і що показується; нотатку можна перенести в іншу тему з поповера. Перейменування й видалення — на сторінці «Мої нотатки» (нотатки видаленої теми стають «Без групи»).
```

Після абзацу «**Посилання з нотатками або без.** …» додай:

```markdown
- **Теми в посиланні.** Для посилання на книгу з нотатками можна обрати теми — отримувач побачить лише їх (без назв тем). Нічого не обрано — усі нотатки. Якщо всі обрані теми видалити, посилання не показуватиме нотаток.
```

У блоці JSON-API після `PATCH  /api/annotations/<id>/ ...` додай:

```
GET    /api/books/<id>/note-groups/     теми поточного користувача в книзі
POST   /api/books/<id>/note-groups/     створити тему {name}
PATCH  /api/note-groups/<id>/           перейменувати тему
DELETE /api/note-groups/<id>/           видалити тему (нотатки → «Без групи»)
```

- [ ] **Step 3: Full verification**

Run:

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test --parallel 4
uvx ruff check PDF+ --select F   # з кореня репозиторію
```

Expected: `No changes detected`; усі тести (~225 + нові) — OK; ruff F — без нових зауважень.

- [ ] **Step 4: Report**

Підсумок користувачу: що зроблено по задачах, вивід тестів, результат ручної перевірки Task 6. Запитати дозволу на коміт (окремою гілкою, бо `master` містить незакомічені зміни користувача).
