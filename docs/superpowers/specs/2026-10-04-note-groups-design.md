# Теми нотаток (групи анотацій) — дизайн

Дата: 2026-10-04 · Статус: погоджено в чаті, чекає перегляду спеки

## Мета

Користувач розкладає нотатки в книзі за темами («Формули», «Питання до вчителя»), щоб різні
теми не перемішувались у читалці. Вчитель може поширити за share-посиланням на книгу лише
обрані теми.

## Погоджені рішення

| Питання | Рішення |
|---|---|
| Масштаб теми | У межах книги: тема належить `Layer` (книга + власник) |
| Скільки тем у нотатки | Не більше однієї; `null` = «Без групи» |
| Де створювати й призначати | У читалці (перемикач + поповер); перейменування й видалення — на сторінці «Мої нотатки» |
| Перемикач у читалці | Один контрол: обрана тема — і куди йдуть нові виділення, і що показується |
| Поширення тем | Лише в share-посиланні на книгу з `include_annotations`; кілька тем; нічого не обрано = усі |
| Посилання на теку, `LayerPublication` | Без змін: віддають весь шар |
| Що бачить отримувач | Нотатки без назв тем |
| Видалення теми | Нотатки лишаються, стають «Без групи» |
| Атрибути теми | Лише назва (до 100 символів), унікальна в межах шару |
| DRF / синк | У цьому ж скоупі, включно з офлайн-створенням тем |

## Модель даних

```python
# annotations/models.py
class NoteGroup(models.Model):
    layer = models.ForeignKey(Layer, on_delete=models.CASCADE, related_name="note_groups")
    name = models.CharField("назва", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "annotations_notegroup"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["layer", "name"], name="unique_note_group_per_layer"),
        ]

class Annotation(models.Model):
    ...
    group = models.ForeignKey(
        NoteGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="annotations",
    )

# sharing/models.py
class ShareLink(models.Model):
    class AnnotationScope(models.TextChoices):
        ALL = "all", "Усі нотатки"
        SELECTED = "selected", "Лише обрані теми"

    annotation_scope = models.CharField(
        max_length=8, choices=AnnotationScope.choices, default=AnnotationScope.ALL,
    )
    note_groups = models.ManyToManyField("annotations.NoteGroup", blank=True, related_name="share_links")
```

Міграції: `annotations/0005_notegroup.py` (модель + `Annotation.group`),
`sharing/0006_sharelink_note_groups.py` (`annotation_scope` + M2M). Наявні посилання
отримують `ALL` — поведінка не змінюється.

**Чому `annotation_scope`, а не «порожній M2M = усі»:** якщо вчитель видалить усі обрані
теми, M2M спорожніє. Тоді посилання почало б віддавати весь шар. Явний режим `SELECTED`
з порожнім набором означає «нічого».

### Видалення теми

Одна функція `annotations/groups.delete_note_group(group)` для вебу й синку:

```python
with transaction.atomic():
    group.annotations.update(group=None, updated_at=timezone.now())
    group.delete()
```

`on_delete=SET_NULL` сам по собі робить масовий `UPDATE` без зміни `updated_at`, і дельта
`api/sync.py` не побачила б, що нотатки звільнились.

## Доступ

`annotations/access.shared_annotations(user, session=None)` — новий єдиний арбітр чужих
нотаток. Джерела ті самі, що в `shared_layers()`, але на рівні `Annotation`:

- публікація для навчальної групи: весь шар (`layer__publications__group__memberships__user=user`,
  власник групи = автор шару, в одному `Q`, як зараз);
- share-посилання з `include_annotations` з сесії:
  - на книгу, `ALL`: `layer.book = link.book`, `layer.owner = link.created_by`;
  - на книгу, `SELECTED`: те саме + `group_id__in=<обрані теми>` (порожній набір — без нотаток);
  - на теку: піддерево, як зараз, без фільтра тем.
- Джерела об'єднуються через OR: два посилання дають об'єднання тем, а публікація перекриває
  обмеження посилання.
- Плюс `layer__book__in=visible_books(user, session)` і виключення власних шарів, як у
  `shared_layers()`.

`_share_link_sources()` повертає Q на рівні шару (для `shared_layers()`) і окремо на рівні
анотації (для `shared_annotations()`). Фільтр тем живе лише в другому. `shared_layers()`
лишається для списку шарів: шар видно, якщо видно хоч одне джерело.

Усі місця, що віддають чужі нотатки, читають їх лише через `shared_annotations()`:
`serialize_shared_layers` / `book_shared_layers`, контекст `book_reader_view`
(`shared_layers_data`), лічильник `teacher_layers[].annotation_count` у `book_detail_view`,
`api/sync.pull_state` (`shared_annotations`). `share_view` нотаток не рендерить — лише
веде в читалку з `?layer=`, тож фільтр тем застосовується там.

## Веб-API читалки (`annotations/api.py`)

| Метод | URL (name) | Дія |
|---|---|---|
| GET | `api/books/<book_pk>/note-groups/` (`api_book_note_groups`) | список тем `{id, name}` |
| POST | те саме | створити `{name}` → 201; шар через `get_or_create` |
| PATCH | `api/note-groups/<pk>/` (`api_note_group_detail`) | перейменувати `{name}` |
| DELETE | те саме | `delete_note_group()` → 204 |

- Вибірка — `NoteGroup.objects.filter(layer__owner=request.user)`; чужа тема дає 404.
  Для GET/POST книга — через `visible_books()`.
- Назва: `annotations/validators.parse_group_name()` (рядок, `strip()`, 1..100 символів).
  Дубль дає 400 «Тема з такою назвою вже є».
- Анотація: `serialize_annotation` додає `"group": id | null`. POST і PATCH приймають
  `group`. `parse_new_annotation` / `parse_annotation_update` перевіряють лише тип
  (додатне ціле або `null`), а `resolve_note_group(layer, raw)` — що тема належить саме
  цьому шару; інакше 400 `group: теми нема або вона з іншої книги`.

## Читалка

`book_reader.html`:

- `#pdf-viewer` отримує `data-note-groups-url` і `data-note-group-url-template`; нових URL
  у JS не хардкодимо.
- `{{ note_groups_data|json_script:"note-groups-data" }}` — стартовий список тем.
- Над `#annotationFilter` — блок `#topicSwitch`: кнопка «Тема: <назва> ▾», меню з пунктами
  «Усі теми», «Без групи», теми книги, «+ Нова тема» (інлайн-поле, Enter — створити).
- Поповер нотатки: селект «Тема» з тими самими пунктами + «+ Нова тема».

`reader.js`:

- Стан `activeTopic`: `"all"` | `"none"` | id теми. Зберігається в `localStorage` під ключем
  `pdfplus:topic:<book_id>` (у `try/catch`); тему, якої вже нема, скидає на `"all"`.
- `isVisible()` = колір не прихований **і** тема збігається (`all` — будь-яка; `none` —
  `group === null`; id — `group === id`). Звідси ж фільтруються і панель, і мітки на
  сторінках, і навігація ↑↓.
- `createHighlight()` додає `group`: id для конкретної теми, `null` для `all` / `none`.
- Створення теми: POST → тема додається в меню й стає активною.
- Перенесення нотатки: PATCH `{group}`; якщо нотатка більше не підходить під фільтр,
  вона зникає з панелі (поповер закривається).
- Для чужого шару (`showLayer` не `own`) `#topicSwitch` прихований, фільтр тем не діє.

## Сторінка «Мої нотатки»

`annotations/views.book_annotations_view`: нотатки згруповані за темою (теми за назвою,
«Без групи» останньою), усередині — за сторінками, як зараз. Біля кожної теми — форма
«Перейменувати» та кнопка «Видалити» з підтвердженням (POST-в'юхи
`note_group_rename_view` / `note_group_delete_view`, `messages` українською, власник через
`layer__owner=request.user`).

## Share-посилання

`ShareLinkForm`:

- Нове поле `note_groups` (`ModelMultipleChoiceField`, чекбокси), queryset — теми шару
  `owner` на обраній книзі. Через те, що книга обирається в тій самій формі, queryset —
  усі теми власника, а `clean()` відкидає теми, чий `layer.book` ≠ `book`.
- `clean()`: теми можна обрати лише за `book` + `include_annotations=True`; інакше
  помилка «Теми можна обрати лише для посилання на книгу з нотатками».
- `save()`: `annotation_scope = SELECTED`, якщо теми обрані, інакше `ALL`.
- У шаблоні форми чекбокси тем показуються JS-ом лише для обраної книги
  (`data-book` на кожному чекбоксі); без JS видно всі, а `clean()` усе одно перевіряє.

`link_list.html`: для `SELECTED` показує «Теми: Формули, Еволюція» (або «теми видалено»,
якщо набір порожній).

## DRF / синк (`api/`)

Pull (`pull_state`):

- `"note_groups"`: **повний** список власних тем щоразу (`NoteGroupSerializer`:
  `id, book, name, updated_at`). Тема, якої нема в списку, вважається видаленою — без
  тумбстоунів.
- `AnnotationSerializer` додає `group`. `SharedAnnotationSerializer` поле `group`
  **не** віддає (учні бачать нотатки без тем).
- `shared_annotations` у pull беруться з `shared_annotations(user)`. Сесії нема, тож
  посилання не діють і фільтр тем фактично не застосовується; публікація — весь шар.

Push (`push_changes`), нова секція обробляється до анотацій:

```json
"note_groups": {
  "create": [{"client_id": "t1", "book": 20, "name": "Еволюція"}],
  "update": [{"id": 5, "name": "Еволюція ссавців"}],
  "delete": [7]
}
```

- Результат: `result["note_groups"] = {"created": [...з client_id], "updated": [...], "deleted": [...]}`;
  помилки — у спільний `result["errors"]` з `op: "group_create" | "group_update" | "group_delete"`.
- Анотації в `create` / `update` приймають `"group": id | null` або `"group_client_id": "t1"`
  (тема, створена в цьому ж батчі; невідомий client_id дає помилку операції).
- `MAX_BATCH_OPS` рахує й операції з темами.
- Видалення — через `delete_note_group()`.

## Адмінка

Без змін. `Layer` показується лише як `LayerInline` у `BookAdmin`
(`book_library/admin.py`), `Annotation` в адмінці не зареєстрована — теми туди теж не
додаємо.

## Тести

Django-раннер; файлові тести — з `FILESYSTEM_STORAGES` і `TEST_MEDIA_ROOT`.

- Модель: унікальність `(layer, name)`; `delete_note_group()` лишає нотатки з
  `group=None` і новішим `updated_at`.
- Веб-API: CRUD тем; 404 на чужу тему; 400 на дубль і порожню назву; 400 на `group` з
  чужого шару чи іншої книги; PATCH переносить нотатку.
- Доступ (`shared_annotations` + `share_view` + `book_shared_layers`):
  `SELECTED` показує лише обрані теми; «Без групи» за `SELECTED` не видно; перенесення
  нотатки з обраної теми ховає її; видалення всіх обраних тем → нуль нотаток; два
  посилання об'єднують теми; `LayerPublication` перекриває обмеження; посилання на теку й
  `ALL` поводяться як раніше.
- Форма посилання: тема з іншої книги, чужа тема, теми без `include_annotations` чи для
  теки — помилка; `annotation_scope` виставляється сам.
- Сторінка «Мої нотатки»: групування; перейменування й видалення лише власником.
- Синк: `note_groups` у pull; push створює тему, нотатка посилається через
  `group_client_id`; дельта після видалення теми віддає звільнені нотатки; учень не
  отримує `group`; ліміт `MAX_BATCH_OPS` враховує теми.
- `manage.py makemigrations --check` чисто; уся сюїта зелена.

## Документація

`CLAUDE.md` (розділ «Анотації»): теми, `shared_annotations()` як арбітр чужих нотаток,
`delete_note_group()` замість прямого `delete()`. `README.md`: опис тем для користувача.

## Поза скоупом

Колір чи іконка теми; теми наскрізь через книги; вибір тем для посилань на теку й для
`LayerPublication`; показ назв тем отримувачам; перетягування нотаток між темами.
