# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Мова

Коментарі, docstring-и, `verbose_name`, повідомлення користувачу й текст у шаблонах — українською.
Ідентифікатори, назви полів і URL-неймспейси — англійською. Тримайся цього поділу в новому коді.

## Команди

Залежності — `uv` (`pyproject.toml` + `uv.lock`) у корені. Django-проєкт — у підтеці `PDF+/`,
тому всі `manage.py`-команди виконуються звідти.

```bash
uv sync                                      # з кореня репозиторію
cd PDF+
cp .env_example .env                         # SECRET_KEY, PG_*, DEBUG=true обов'язкові
uv run python manage.py migrate
uv run python manage.py runserver
```

Тести (Django-раннер, не pytest — pytest у залежностях нема):

```bash
cd PDF+
uv run python manage.py test                       # уся сюїта, ~225 тестів
uv run python manage.py test --parallel 4          # швидше
uv run python manage.py test book_library          # один застосунок
uv run python manage.py test book_library.tests.UploadValidationTests
uv run python manage.py test book_library.tests.UploadValidationTests.test_password_protected_pdf_rejected
```

Лінт — `ruff` не встановлений у `.venv`, запускається через `uvx`:

```bash
uvx ruff check PDF+ --select F               # реальні проблеми (unused, undefined)
uvx ruff check PDF+                          # повний прогін; конфігу нема, тому
                                             # більшість зауважень — E501 на дефолтних 88 символів,
                                             # а фактична ширина рядків у проєкті до ~100+
```

Бекфіл обкладинок для книг, залитих до появи поля `cover`:

```bash
cd PDF+ && uv run python manage.py render_covers
```

## Архітектура

Монолітний Django 6.1 із серверним рендером, PostgreSQL, п'ять застосунків.
Розгорнутий опис функціоналу — у `README.md`; тут лише те, що не видно з одного файлу.

### Доступ вирішується в одному місці

`book_library/access.py` — єдиний арбітр видимості. В'юхи **не перевіряють права поштучно**,
вони звужують queryset:

```python
book = get_object_or_404(visible_books(request.user, request.session), pk=pk)
```

Забутий фільтр не кидає виняток — він тихо відкриває чужі дані. Будь-яка нова в'юха,
що читає `Book` чи `Folder`, має йти через `visible_books()` / `visible_folders()`.

Чотири джерела права на перегляд: власність, `GroupAccess` через членство в групі,
відкритий `ShareLink` (токен лежить у сесії, ключ `share_tokens`), тека каталогу
(`is_catalog`). Право на зміну — лише у власника; `GroupAccess.EDIT` існує в моделі,
але прав на матеріал поки не додає.

Доступ до теки транзитивний: `_with_descendants()` обходить дерево BFS-ом на кожен запит
(closure-таблиці нема). Нове джерело доступу треба вливати в той самий набір id, інакше
воно не пошириться на піддерево.

`request.session` передається в `access`-функції окремим аргументом саме тому, що
share-токени живуть у сесії. Там, де сесії нема (DRF API), передається `None` — і
посилання за токеном там не діють за побудовою.

### Пошук

`book_library/views._search_titles()` **повертає список, а не queryset** — case-insensitive
порівняння робиться в Python, бо база створена з локаллю `C`, де `ILIKE`/`UPPER()` не
складають кирилицю. Дочіплювати `.filter()` до результату не можна. `api/views.py` імпортує
цю саму функцію, щоб пошук у REST і на сайті збігався.

### Завантаження PDF

`book_library/pdf.inspect_pdf()` — спільний шлях для форми на сайті та адмінки: розмір,
сигнатура `%PDF-`, розбір через PyMuPDF, відсів захищених паролем і порожніх, підрахунок
сторінок, наявність текстового шару, рендер обкладинки. Нову точку завантаження заводь
через нього, а не дублюй перевірки.

### Файли у сховищі

Абстраговані через Django STORAGES: локально — файлова система, з `USE_R2=true` — приватний
бакет Cloudflare R2 (`storages.backends.s3.S3Storage`). Бакет **приватний навмисне**: право
на книгу перевіряє застосунок, тому `file.url` віддає підписане посилання на 3600 с.
Публічний бакет обійшов би і групи, і share-токени.

`book_library/signals.py` закриває дві течі, яких Django не закриває сам: `post_delete`
(видалення моделі) і `pre_save` (заміна файлу). Обидві відкладені через
`transaction.on_commit`, щоб відкат не лишав книгу без файлу. Нове FileField на `Book`
треба додати в `BLOB_FIELDS`.

### Анотації

`Layer` — один на пару (книга, користувач), унікальність на рівні БД. Координати
зберігаються як список прямокутників у PDF user space, тому масштаб читалки на них не
впливає. Палітра кольорів живе в `Annotation.PALETTE` — шаблон і валідатор беруть її звідти.

`annotations/validators.py` перевіряє кожне поле payload'у перед записом у `JSONField`
(структура прямокутників, ліміти `MAX_RECTS`/`MAX_NOTE_LENGTH`, hex-колір). `PayloadError`
в'юха перетворює на 400. Той самий валідатор використовує батч-пуш у `api/sync.py`.

Теми нотаток — `NoteGroup` (FK на `Layer`, унікальна назва в межах шару);
`Annotation.group` — `SET_NULL`, `null` = «Без групи». Операції з темами —
`annotations/groups.py`, спільний для вебу й синку: `group_fields()` розв'язує `group` /
`group_client_id` з payload (тема лише з шару цього власника на цій книзі), а
**видаляти тему тільки через `delete_note_group()`** — голий `delete()` через `SET_NULL`
не оновить `updated_at`, і дельта синку не побачить звільнених нотаток.

Шар можна опублікувати для груп (`LayerPublication`). Чужі шари перелічує
`annotations/access.shared_layers()`, а чужі **нотатки** — лише `shared_annotations()`:
share-посилання на книгу з `annotation_scope="selected"` відкриває тільки обрані теми
(`ShareLink.note_groups`; порожній набір — нічого, не весь шар). Джерела: членство в групі, **власник якої — автор шару**, або
share-посилання з `include_annotations` (шар автора посилання на книзі чи піддереві теки;
працює й для анонімів, бо токен у сесії), плюс книга в `visible_books()` — відкликаний
доступ до книги ховає й шар. Публікувати можна лише
в `publishable_groups()`. Редагування й видалення анотацій і далі фільтруються за
`layer__owner=request.user`, тож чужий шар лишається лише для читання. У `reader.js` зміни
йдуть у власну `annotations`, а на екрані — `shown` (власний або обраний шар вчителя).

Читалка `static/js/reader.js` (ES-модуль, PDF.js вендорений у `static/vendor/pdfjs`)
отримує всі URL і CSRF-токен через `data-`атрибути `#pdf-viewer` — нових ендпоінтів
у JS не хардкодь, передавай через шаблон.

### Прогрес читання

`book_library.ReadingProgress` — одна позиція на пару (користувач, книга). Пиши лише через
`book_library/progress.record_progress()`: там last-write-wins за `read_at` (час, коли читач
був на сторінці), а не за часом запиту, інакше офлайн-клієнт затирає новішу позицію. Читай
через `visible_progress()` — він звужує за `visible_books()`. `updated_at` потрібен лише
для дельти `api/sync.py`. У `reader.js` прапорець `tracking` вимкнений на старті й під час
`reflow()`: в'юер у цей момент стоїть на с. 1, і трекер перезаписав би прогрес.

### Два різні API

- `annotations/api.py` — старий JSON-API читалки: звичайні Django-в'юхи, сесійна
  автентифікація, CSRF як у форм. На DRF **не переведений**.
- `api/` — DRF + SimpleJWT для мобільного/офлайн-клієнта. Сесій і CSRF нема, тому
  share-посилання тут не працюють. Blacklist не ввімкнений: refresh відкликати достроково
  не можна. `api/sync.py` — дельта-синхронізація; видалені анотації фіксуються
  тумбстоунами (`api.DeletedAnnotation` через `post_delete`, з `layer_id` — за ним учні
  отримують видалення з шару вчителя), а `visible_*_ids` щоразу
  їдуть повністю, щоб клієнт викинув те, доступ до чого відкликали.
  `API_SYNC_OVERLAP_SECONDS` відсуває `since` назад — паралельні коміти інакше губляться.

### Адмінка

Власний `AdminSite` (`book_sharing_settings/admin_site.py`, реєструється як
`AdminConfig.default_site`), Unfold стоїть перед `django.contrib.admin` в `INSTALLED_APPS`.
Шлях адмінки береться з `ADMIN_SITE_URL` у `.env`, тому `/admin/` на проді не існує —
не покладайся на цей префікс у коді чи тестах, читай `settings.ADMIN_URL`.
Дашборд і 30-денний графік — `book_sharing_settings/dashboard.py`.

## Конвенції

- Кожна модель явно задає `Meta.db_table` (`accounts_user`, `library_book`,
  `annotations_layer`, `sharing_groupaccess`…). Нові моделі теж.
- Тести, що торкаються файлів, **обов'язково** ізолюються, інакше з `USE_R2=true` у `.env`
  вони підуть у справжній бакет:

  ```python
  from book_sharing_settings.testing import FILESYSTEM_STORAGES

  @override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT, STORAGES=FILESYSTEM_STORAGES)
  class MyTests(TestCase):
      ...
  ```

  `TEST_MEDIA_ROOT` створюється через `tempfile.mkdtemp()` на рівні модуля.
- Ключ файлу у сховищі — випадковий UUID (`book_upload_path`), не ім'я від користувача.
- Пошта — через `MAILERS` (Django 6.1), а не застарілі `EMAIL_*`; env-змінні тому названі
  `SMTP_*`. Порожній `SMTP_HOST` = console-бекенд. Тест-раннер сам підміняє кожен аліас
  `MAILERS` на locmem, тож `mail.outbox` у тестах працює без `override_settings`.
- Вбудовані auth-в'юхи Django (`PasswordReset*View`, `PasswordChangeView`) редиректять на
  URL без неймспейсу — у підкласах в `users/views.py` `success_url` вказує на `accounts:…`.
- `LANGUAGE_CODE = 'uk'`: вбудовані повідомлення Django й DRF — українською.
- Sentry ініціалізується лише за непорожнього `SENTRY_DSN`; локально й у тестах DSN
  порожній і жодного мережевого трафіку нема. `send_default_pii=False` — не вмикай без причини.
- Редиректи за `?next=` проходять через `_safe_next()` (`url_has_allowed_host_and_scheme`).
