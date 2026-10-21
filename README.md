# PDF+

A web application for storing, reading and sharing PDF books in an educational setting.
A teacher uploads materials, organizes them into folders, and grants access to classes or via a link.
Students read books right in the browser and make their own highlights with notes without modifying anyone else's files.

## Contents

- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Features](#features)
- [Access model](#access-model)
- [Project structure](#project-structure)
- [Running locally](#running-locally)
- [Tests](#tests)

## Tech stack

### Backend

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.13 | application language |
| Django | 6.1 | web framework, ORM, sessions, authentication, admin |
| PostgreSQL + psycopg 3 | 3.3 | primary database |
| PyMuPDF (`pymupdf`) | 1.28 | validating uploaded PDFs: signature, page count, password, presence of a text layer |
| Authlib | 1.8 | Google sign-in (OpenID Connect, metadata from the discovery document) |
| django-storages + boto3 | 1.14 | storing PDFs in Cloudflare R2 via the S3-compatible API |
| django-unfold | 0.106 | modern admin with a dashboard and an activity chart |
| django-crispy-forms + crispy-bootstrap5 | 2.7 | rendering forms in Bootstrap 5 style |
| Django REST Framework + simplejwt | 3.18 / 5.5 | REST API with JWT for the mobile / offline client (`api/`) |
| python-decouple | 3.8 | configuration via `.env` |
| sentry-sdk[django] | 2.69 | error tracking and performance traces in production |
| uv | — | dependency manager (`pyproject.toml`, `uv.lock`) |
| ruff | 0.16 | linter |

`django-redis` is also among the dependencies, but the cache is not configured yet. The reader's
JSON API (`annotations/api.py`) is also built on DRF, but uses session authentication and CSRF instead of JWT.

### Frontend

| Technology | Version | Purpose |
|---|---|---|
| Django Templates | — | server-side page rendering, shared `base.html` with a sidebar |
| Bootstrap 5 + Bootstrap Icons | 5.3 / 1.11 | grid, components, icons (from a CDN) |
| PDF.js | 6.3 | rendering PDFs into `<canvas>` and a text layer for selection (vendored in `static/vendor/pdfjs`) |
| Vanilla JS (ES modules) | — | the reader `static/js/reader.js`: pages, zoom, palette, note popup, color filter |
| Chart.js (via Unfold) | — | activity chart on the admin dashboard |

### Infrastructure

- **Cloudflare R2** — a private bucket for PDFs. Files are served via short-lived signed URLs, because access to a book is checked by the application, not by the storage. Enabled with `USE_R2=true`; otherwise files live in `PDF+/media/`.
- **Sentry** — the single error tracker. An empty `SENTRY_DSN` disables the SDK entirely. No PII is sent in events.
- **Vercel** — the target deployment platform (the Sentry release is taken from `VERCEL_GIT_COMMIT_SHA`).
- **Google OAuth** — accounts are matched by email, so email is unique in the user model.
- **SMTP** — password reset emails. Configured via `MAILERS` (Django 6.1); an empty `SMTP_HOST` prints emails to the console.

## Architecture

A classic monolithic Django project with server-side rendering. Five apps:

| App | Responsibility |
|---|---|
| `users` | custom `User` model, registration, password and Google sign-in, profile |
| `book_library` | folders (a tree via a self-FK), books, PDF upload and validation, reader, catalog, access rules |
| `sharing` | groups (classes), membership, group access to a book or folder, token links |
| `annotations` | annotation layers (one layer per user and book), highlights with notes, JSON API for the reader |
| `api` | DRF REST API: JWT, catalog, book file, delta sync for the offline client |
[![Architecture diagram](https://gitdiagram.com/diagram-badge.svg)](https://gitdiagram.com/illyadd/pdf-share?utm_source=readme&utm_medium=badge)

Key internal modules:

- `book_library/access.py` — the single place that decides who sees what. All views filter querysets through `visible_books()` / `visible_folders()`.
- `book_library/pdf.py` — `inspect_pdf()` for both the site form and the admin: the same validation no matter where the file comes from.
- `book_library/signals.py` — deletes files from storage after `post_delete` and on file replacement (`pre_save`), deferred until the transaction commits.
- `annotations/validators.py` — strict validation of the reader's payload before writing it into a `JSONField`.
- `book_sharing_settings/dashboard.py` — statistics and the 30-day chart for the admin home page.

## Features

### Users

- Sign up with a password or sign in with Google. A Google account has no password.
- There are no roles: every user can upload books, create groups and share. Permissions on materials are determined by ownership and sharing.
- Profile: view, edit, change password, delete account.
- Password reset by email (`/accounts/password/reset/`): the link is valid for one day and works only once. Google accounts don't receive the email — they have no password. An unknown email gets the same response as a known one, so the form can't be used to check whether an address is registered.

### Library

- A folder tree of arbitrary depth. Breadcrumbs and a full path ("Parent / Child / Grandchild") for folders with the same name.
- PDF upload via drag-and-drop. 50 MB limit. Files are validated for real: the `%PDF-` signature, parsing with PyMuPDF, rejecting password-protected and empty documents.
- On upload, the page count, file size, title from the PDF metadata and a text-layer flag are stored (the flag determines whether highlighting will work).
- The file's storage key is a random UUID, not the user-supplied name.
- Rename, replace file, move between folders, delete. Bulk-add books to a folder.
- Case-insensitive title search with Cyrillic support. Inside a folder, search covers the whole subtree.
- Sections: "My library", "Shared with me", "Catalog".

### Catalog

An administrator marks a folder with the `is_catalog` flag. It becomes visible, together with everything nested in it, to every signed-in user. Catalog search covers all of its contents.

### Sharing

- **Groups (classes).** A teacher creates a group, adds members, and grants the group access to a book or folder. Access to a folder extends to the whole tree beneath it.
- **Token links.** The owner creates a link to a book or folder, with or without an expiry date. The token is generated with `secrets.token_urlsafe(32)`. An opened link is remembered in the session, so navigating between books keeps working without re-entering it.
- **Links with or without notes.** When creating a link, the author chooses "Book only" (default) or "Book with my notes". The latter gives the recipient — even an anonymous one — read-only access to the author's layer on the book (for a folder, on every book in the subtree), and the button on the link page leads straight into that layer. The link does not expose other users' layers on the same book.
- **Topics in a link.** For a book link with notes, specific topics can be selected — the recipient will see only those (without topic names). Nothing selected means all notes. If all selected topics are deleted, the link will show no notes.
- A list of your own links and grants, with the option to revoke them.

### Reader

- Pages rendered by PDF.js into canvas, lazy-loaded via `IntersectionObserver`.
- Zoom from 50% to 300%, jump to a page number.
- Text highlighting with a palette of five light colors (`mix-blend-mode: multiply`, text stays readable).
- A note on each highlight. A side panel listing annotations and a color filter.
- Note topics within a book: the "Topic" switch in the reader determines where new highlights go and what is shown; a note can be moved to another topic from the popover. Renaming and deleting topics happen on the "My notes" page (notes of a deleted topic become "Ungrouped").
- Annotations are stored as a list of rectangles in PDF user space, so they scale correctly.
- Anyone who can see a book can read it. Only signed-in users can annotate. Each user's layer is separate and never modifies someone else's book.
- Reading progress: the page field in the toolbar follows scrolling, the current page is saved
  (with a 1.5 s delay and when the tab is closed), and the next time the book opens it starts there.
  The home page has a "Continue reading" block; the book page shows progress and a "Continue from p. N" button.
- Teacher layer for a class: the group owner can publish their layer on the book page to selected groups
  (only those whose students can see the book — via access to the book, to a folder above it, or the catalog).
  Students get a "Mine / *teacher's name*" switch in the reader: the other layer is read-only, with no
  edit popup. Revoking access to the book also hides the layer.

Reader JSON API (session authentication, CSRF as for forms):

```
GET    /api/books/<id>/annotations/     list the current user's annotations
GET    /api/books/<id>/shared-layers/   teachers' layers published to the user's groups
POST   /api/books/<id>/annotations/     create a highlight
PATCH  /api/annotations/<id>/           change color, note or topic (group)
DELETE /api/annotations/<id>/           delete
GET    /api/books/<id>/note-groups/     the current user's topics in the book
POST   /api/books/<id>/note-groups/     create a topic {name}
PATCH  /api/note-groups/<id>/           rename a topic
DELETE /api/note-groups/<id>/           delete a topic (notes → "Ungrouped")
POST   /books/<id>/progress/            {page} — save the current page (204)
```

### REST API (DRF, JWT)

For the mobile / offline client. Header: `Authorization: Bearer <access>`. The token
is verified by its signature; the server doesn't hit the database. Access tokens live 30 minutes, refresh tokens 30 days,
and are rotated on every refresh. The blacklist is not enabled: a refresh token can't be revoked early.
Issuing and refreshing tokens is limited to 10 requests per minute per IP address.
There are no sessions or CSRF here, so share links (source 3 in the access model) don't work in the API.

```
POST   /api/auth/token/          {username, password} → {access, refresh, user}
POST   /api/auth/token/refresh/  {refresh} → {access, refresh}
GET    /api/catalog/books/       catalog, paginated by 50, ?search= by title
GET    /api/books/<id>/file/     302 to the file in storage (with R2 — a signed URL)
GET    /api/sync/?since=<ISO>    state delta: books, folders, annotations (own),
                                 deleted_annotation_ids, note_groups (own topics),
                                 reading_progress, shared_layers, shared_annotations
                                 (teachers' layers), visible_book_ids, visible_folder_ids,
                                 server_time
POST   /api/sync/                batch {note_groups: {create, update, delete},
                                        annotations: {create, update, delete},
                                        reading_progress: [{book, last_page, read_at}]}
```

Without `since`, the pull returns the full state. The client stores `server_time` as the next `since`
and drops locally everything missing from `visible_*_ids` (revoked access, deleted book).
Deleted annotations are recorded as tombstones (`api.DeletedAnnotation`, via `post_delete`);
`deleted_annotation_ids` also includes deletions from published teacher layers.
`shared_layers` is always sent in full: a layer that disappears from the list is dropped by the client along with its annotations.
A layer that became visible after `since` (new publication, joining a group, access to the book)
arrives in full in `shared_annotations`.
In a push, every operation is independent: errors are returned per item in `errors`, conflicts are
resolved as last-write-wins. For reading progress, the winner is determined by `read_at` (when the client
was on the page), not by when the push arrived: an offline phone won't overwrite a newer position from the web.
The `reading_progress` in the response is the actual state after merging.

Note topics in sync:

- `note_groups` in a pull is the **full** list of your own topics (`id, book, name, created_at,
  updated_at`) every time, even in a delta; a topic missing from the list has been deleted. There are no
  tombstones for topics. Annotations have a `group` field (topic `id` or `null` — "Ungrouped");
  it is absent in `shared_annotations` — students see the teacher's notes without the teacher's topics.
- In a push, `note_groups.create` is `[{client_id, book, name}]`, `update` is `[{id, name}]`,
  `delete` is `[id]`. Creating by name is idempotent: if a topic with that name already exists in this
  book (another device, a repeated push), that same topic is returned with your `client_id`.
  Renaming to a name already taken is a `group_update` operation error.
- An annotation in `create`/`update` accepts `group: id | null` or `group_client_id` — a topic
  created in the same push. The topic must belong to the same book.
- Processing order: topic deletions → renames → creations → annotations → progress.
  So "delete or rename X and create a new X" in one push yields a new topic.
  If a note references a topic deleted in the same push, it becomes "Ungrouped",
  and the rest of its changes are kept. Deleting a topic doesn't delete its notes — the server makes them
  "Ungrouped" and bumps `updated_at`, so they arrive in the next delta.
- The push result is `note_groups: {created, updated, deleted}`; errors go into the shared
  `errors` with `op` `group_create` / `group_update` / `group_delete`. Topic operations
  count toward the batch limit.

### Admin

- Unfold with a custom `AdminSite`. The admin path is set in `.env` (`ADMIN_SITE_URL`), so `/admin/` isn't exposed in production.
- Dashboard: number of users, group owners, group members, books, folders, annotations, groups, active links. A 30-day chart with three series: new users, books, annotations.
- Environment badge (Development / Production).
- PDF uploads through the admin go through the same validation as on the site.

## Access model

Permission to view a book or folder comes from any of four sources:

1. **Ownership** — the user is the `owner`.
2. **Group** — the material is granted to a group via `GroupAccess`, and the user is a member.
3. **Link** — the user has opened a `ShareLink`, and the token is stored in the session.
4. **Catalog** — a folder with `is_catalog=True` or one of its descendants.

Only the owner can modify (rename, delete, replace the file). `GroupAccess.EDIT` exists in the model but doesn't yet grant any extra rights on the material itself.

Files in R2 are private. A file link is signed for 3600 s (configurable via `R2_URL_EXPIRE`), so a direct link can't bypass the access check.

## Project structure

```
MAN-PDF-Share-Prod/
├── pyproject.toml, uv.lock        dependencies
└── PDF+/
    ├── manage.py
    ├── book_sharing_settings/     settings, urls, oauth, admin_site, dashboard
    ├── users/                     User, registration, Google OAuth
    ├── book_library/              folders, books, pdf.py, access.py, signals.py, reader
    ├── sharing/                   groups, membership, access grants, links
    ├── annotations/               layers, annotations, api.py, validators.py
    ├── api/                       REST API (JWT), delta sync
    ├── templates/                 base.html, nav, sidebar, partials
    ├── static/
    │   ├── css/app.css
    │   ├── js/reader.js
    │   └── vendor/pdfjs/
    └── media/                     local PDF storage (without R2)
```


## Running locally

Requires PostgreSQL and Python 3.13. Redis is optional.

```bash
uv sync
cp PDF+/.env_example PDF+/.env      # fill in SECRET_KEY, PG_*, DEBUG=true
cd PDF+
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

Environment variables are described in `PDF+/.env_example`. Google sign-in needs `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`. R2 needs `USE_R2=true` and the `R2_*` keys. Email needs `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `DEFAULT_FROM_EMAIL`.

## Tests

```bash
cd PDF+
uv run python manage.py test
```

Tests never touch R2: storage is forced onto a temporary `MEDIA_ROOT` (`book_sharing_settings/testing.py`). Covered: access rules, PDF validation, sharing, annotations, authentication and the admin.
