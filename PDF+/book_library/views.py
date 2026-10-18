import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from annotations.access import publishable_groups, shared_annotations, shared_layers
from annotations.forms import LayerPublishForm
from annotations.models import Annotation, Layer
from annotations.serializers import (
    ReaderAnnotationSerializer,
    ReaderNoteGroupSerializer,
    serialize_shared_layers,
)
from annotations.validators import MAX_NOTE_LENGTH, PayloadError, parse_page_number
from sharing.models import Group, GroupAccess, ShareLink

from .access import (
    catalog_folder_ids,
    catalog_root_folders,
    folder_subtree_ids,
    is_catalog_folder,
    visible_books,
    visible_folders,
)
from .forms import BookForm, FolderForm
from .models import Book, Folder, ReadingProgress
from .progress import record_progress, visible_progress


def _folder_crumbs(folder):
    chain = []
    node = folder
    while node is not None:
        chain.append(node)
        node = node.parent

    if any(node.is_catalog for node in chain):
        crumbs = [{"url": reverse("book_library:catalog"), "label": _("Каталог")}]
    else:
        crumbs = [{"url": reverse("book_library:library"), "label": _("Бібліотека")}]

    for node in reversed(chain):
        crumbs.append({"url": reverse("book_library:folder", args=[node.pk]), "label": node.name})
    return crumbs


def _folder_choices(user):
    folders = list(Folder.objects.filter(owner=user).select_related("parent"))
    by_pk = {f.pk: f for f in folders}
    for folder in folders:
        if folder.parent_id and folder.parent_id in by_pk:
            folder.parent = by_pk[folder.parent_id]
    return sorted(
        ({"pk": f.pk, "label": f.path_label} for f in folders),
        key=lambda item: item["label"].lower(),
    )


def _search_titles(queryset, query, field="title"):
    needle = query.casefold()
    return [obj for obj in queryset if needle in getattr(obj, field).casefold()]


# Де шукати в «Моїй бібліотеці»: значення параметра ?scope= і підпис у селекті.
SEARCH_SCOPES = [
    ("all", gettext_lazy("Усюди")),
    ("folders", gettext_lazy("Теки")),
    ("books", gettext_lazy("Книги")),
]


def _safe_next(request, fallback):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


def home_view(request):
    continue_reading = []
    if request.user.is_authenticated:
        continue_reading = (
            visible_progress(request.user, request.session)
            .select_related("book")
            .order_by("-read_at")[:6]
        )
    return render(request, "book_library/home.html", {"continue_reading": continue_reading})


def welcome_view(request):
    """Огляд можливостей для нових користувачів; сюди веде реєстрація без ?next=."""
    return render(request, "book_library/welcome.html")


@login_required
def library_view(request, pk=None):
    folder = None
    is_owner = True
    query = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "all")
    if scope not in dict(SEARCH_SCOPES):
        scope = "all"

    if pk is not None:
        folder = get_object_or_404(visible_folders(request.user, request.session), pk=pk)
        is_owner = folder.owner_id == request.user.pk
        folders = visible_folders(request.user, request.session).filter(parent=folder)
        books = visible_books(request.user, request.session).filter(folder=folder)
        subtree_ids = folder_subtree_ids(folder.pk)
        book_pool = visible_books(request.user, request.session).filter(folder_id__in=subtree_ids)
        # Сама тека — не результат пошуку в ній, лише вкладені.
        folder_pool = visible_folders(request.user, request.session).filter(
            pk__in=subtree_ids - {folder.pk}
        )
    else:
        folders = Folder.objects.filter(owner=request.user, parent=None)
        books = Book.objects.filter(owner=request.user, folder=None)
        book_pool = Book.objects.filter(owner=request.user)
        folder_pool = Folder.objects.filter(owner=request.user)

    if query:
        folders = []
        books = []
        if scope in ("all", "folders"):
            folders = _search_titles(
                folder_pool.select_related("parent__parent").order_by("name"), query, "name"
            )
        if scope in ("all", "books"):
            books = _search_titles(
                book_pool.select_related("folder__parent__parent").order_by("title"), query
            )

    return render(
        request,
        "book_library/library.html",
        {
            "folder": folder,
            "folders": folders,
            "books": books,
            "query": query,
            "search_scope": scope,
            "search_scopes": SEARCH_SCOPES,
            "result_count": len(folders) + len(books) if query else 0,
            "is_owner": is_owner,
            "in_catalog": is_catalog_folder(folder) if folder else False,
            "crumbs": _folder_crumbs(folder) if folder else None,
            "page_title": folder.name if folder else _("Моя бібліотека"),
            "upload_url": reverse("book_library:book_upload"),
            "add_books_url": reverse("book_library:folder_add_books", args=[folder.pk])
            if folder and is_owner
            else "",
            "move_folders": _folder_choices(request.user) if is_owner else [],
        },
    )


@login_required
def shared_with_me_view(request):
    catalog_ids = catalog_folder_ids()
    shared_books = visible_books(request.user, request.session).exclude(owner=request.user)
    shared_folders = visible_folders(request.user, request.session).exclude(owner=request.user)
    if catalog_ids:
        shared_books = shared_books.exclude(folder_id__in=catalog_ids)
        shared_folders = shared_folders.exclude(pk__in=catalog_ids)

    return render(
        request,
        "book_library/shared_with_me.html",
        {
            "shared_books": shared_books,
            "shared_folders": shared_folders,
            "page_title": _("Поділилися зі мною"),
        },
    )


@login_required
def catalog_view(request):
    query = request.GET.get("q", "").strip()
    folders = catalog_root_folders()
    books = []

    if query:
        folders = Folder.objects.none()
        books = _search_titles(
            Book.objects.filter(folder_id__in=catalog_folder_ids())
            .select_related("folder__parent__parent")
            .order_by("title"),
            query,
        )

    return render(
        request,
        "book_library/catalog.html",
        {
            "folders": folders,
            "books": books,
            "query": query,
            "page_title": _("Каталог"),
            "page_subtitle": _("Матеріали, доступні всім користувачам"),
        },
    )


@login_required
def folder_create_view(request):
    form = FolderForm(request.POST if request.method == "POST" else None, owner=request.user)
    if request.method == "GET":
        parent_id = request.GET.get("parent")
        if parent_id:
            form.initial["parent"] = parent_id

    if request.method == "POST" and form.is_valid():
        folder = form.save(commit=False)
        folder.owner = request.user
        folder.save()
        messages.success(request, _("Теку створено."))
        return redirect("book_library:folder", pk=folder.pk)

    return render(
        request,
        "book_library/folder_form.html",
        {
            "form": form,
            "page_title": _("Нова тека"),
            "form_title": _("Нова тека"),
            "cancel_url": reverse("book_library:library"),
        },
    )


@login_required
def folder_edit_view(request, pk):
    folder = get_object_or_404(Folder, pk=pk, owner=request.user)
    form = FolderForm(request.POST if request.method == "POST" else None, instance=folder, owner=request.user)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Теку оновлено."))
        return redirect("book_library:folder", pk=folder.pk)

    return render(
        request,
        "book_library/folder_form.html",
        {
            "form": form,
            "page_title": _("Редагування теки"),
            "form_title": _("Тека «%(name)s»") % {"name": folder.name},
            "cancel_url": reverse("book_library:folder", args=[folder.pk]),
        },
    )


@login_required
def folder_delete_view(request, pk):
    folder = get_object_or_404(Folder, pk=pk, owner=request.user)
    if request.method == "POST":
        parent_pk = folder.parent_id
        folder.delete()
        messages.success(request, _("Теку видалено."))
        if parent_pk:
            return redirect("book_library:folder", pk=parent_pk)
    return redirect("book_library:library")


@login_required
def folder_add_books_view(request, pk):
    folder = get_object_or_404(Folder, pk=pk, owner=request.user)
    query = request.GET.get("q", "").strip()

    candidates = (
        Book.objects.filter(owner=request.user)
        .exclude(folder=folder)
        .select_related("folder__parent__parent")
        .order_by("title")
    )
    if query:
        candidates = _search_titles(candidates, query)

    return render(
        request,
        "book_library/folder_add_books.html",
        {
            "folder": folder,
            "books": candidates,
            "query": query,
            "crumbs": _folder_crumbs(folder) + [{"url": "", "label": _("Додати книги")}],
            "page_title": _("Додати книги до «%(name)s»") % {"name": folder.name},
        },
    )


def _publish_form(user, book, layer):
    if not user.is_authenticated or not Group.objects.filter(owner=user).exists():
        return None
    published = list(layer.shared_with_groups.values_list("pk", flat=True)) if layer else []
    return LayerPublishForm(groups=publishable_groups(user, book), initial={"groups": published})


def book_detail_view(request, pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=pk)

    is_owner = request.user.is_authenticated and book.owner_id == request.user.pk
    layer = (
        Layer.objects.filter(book=book, owner=request.user).first()
        if request.user.is_authenticated
        else None
    )
    # Лічильник — лише з нотаток, які читач бачить (посилання може відкривати не всі теми).
    shared = shared_annotations(request.user, request.session).filter(layer__book=book)
    shared_counts = dict(
        Annotation.objects.filter(pk__in=shared.values("pk"))
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

    progress = (
        ReadingProgress.objects.filter(book=book, user=request.user).first()
        if request.user.is_authenticated
        else None
    )

    return render(
        request,
        "book_library/book_detail.html",
        {
            "book": book,
            "is_owner": is_owner,
            "progress": progress,
            "annotation_count": Annotation.objects.filter(layer=layer).count() if layer else 0,
            "teacher_layers": teacher_layers,
            "publish_form": _publish_form(request.user, book, layer),
            "share_links": ShareLink.objects.filter(book=book, created_by=request.user)
            if is_owner
            else ShareLink.objects.none(),
            "group_accesses": GroupAccess.objects.filter(book=book).select_related("group")
            if is_owner
            else GroupAccess.objects.none(),
            "crumbs": _folder_crumbs(book.folder) + [{"url": "", "label": book.title}],
            "page_title": book.title,
            "move_folders": _folder_choices(request.user) if is_owner else [],
        },
    )


def book_reader_view(request, pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=pk)

    layer = (
        Layer.objects.filter(book=book, owner=request.user).first()
        if request.user.is_authenticated
        else None
    )
    annotations = (
        Annotation.objects.filter(layer=layer).select_related("layer")
        if layer
        else Annotation.objects.none()
    )
    note_groups = list(layer.note_groups.all()) if layer else []
    progress = (
        ReadingProgress.objects.filter(book=book, user=request.user).first()
        if request.user.is_authenticated
        else None
    )

    return render(
        request,
        "book_library/book_reader.html",
        {
            "book": book,
            "progress": progress,
            "annotations": annotations,
            "login_url": reverse("accounts:login"),
            "palette": Annotation.PALETTE,
            "max_note_length": MAX_NOTE_LENGTH,
            "annotations_data": ReaderAnnotationSerializer(annotations, many=True).data,
            "note_groups_data": ReaderNoteGroupSerializer(note_groups, many=True).data,
            "shared_layers_data": serialize_shared_layers(
                shared_layers(request.user, request.session).filter(book=book),
                shared_annotations(request.user, request.session).filter(layer__book=book),
            ),
        },
    )


@login_required
@require_POST
def book_progress_view(request, pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            raise PayloadError(_("очікується JSON-об'єкт"))
        page = parse_page_number(payload.get("page"), book.page_count, field="page")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("тіло запиту не є коректним JSON")}, status=400)
    except PayloadError as error:
        return JsonResponse({"error": str(error)}, status=400)

    record_progress(request.user, book, page)
    return HttpResponse(status=204)


@login_required
def book_upload_view(request):
    form = BookForm(request.POST if request.method == "POST" else None, request.FILES if request.method == "POST" else None, owner=request.user)
    if request.method == "GET":
        folder_id = request.GET.get("folder")
        if folder_id:
            form.initial["folder"] = folder_id

    if request.method == "POST" and form.is_valid():
        book = form.save(commit=False)
        book.owner = request.user
        book.save()
        messages.success(request, _("«%(title)s» завантажено: %(pages)s с.") % {"title": book.title, "pages": book.page_count})
        return redirect("book_library:book_detail", pk=book.pk)

    return render(
        request,
        "book_library/book_form.html",
        {
            "form": form,
            "page_title": _("Завантаження PDF"),
            "form_title": _("Завантажити PDF"),
            "form_hint": _("Приймаються файли у форматі PDF."),
            "cancel_url": reverse("book_library:library"),
        },
    )


@login_required
def book_edit_view(request, pk):
    book = get_object_or_404(Book, pk=pk, owner=request.user)
    form = BookForm(request.POST if request.method == "POST" else None, request.FILES if request.method == "POST" else None, instance=book, owner=request.user)

    if request.method == "POST" and form.is_valid():
        book = form.save()
        messages.success(request, _("Книгу оновлено."))
        return redirect("book_library:book_detail", pk=book.pk)

    return render(
        request,
        "book_library/book_form.html",
        {
            "form": form,
            "page_title": _("Редагування книги"),
            "form_title": f"«{book.title}»",
            "cancel_url": reverse("book_library:book_detail", args=[book.pk]),
        },
    )


@login_required
def book_delete_view(request, pk):
    book = get_object_or_404(Book, pk=pk, owner=request.user)
    if request.method == "POST":
        folder_pk = book.folder_id
        book.delete()
        messages.success(request, _("Книгу видалено."))
        if folder_pk:
            return redirect("book_library:folder", pk=folder_pk)
    return redirect("book_library:library")


@login_required
@require_POST
def book_move_view(request, pk):
    book = get_object_or_404(Book, pk=pk, owner=request.user)

    raw_folder = request.POST.get("folder", "").strip()
    folder = None
    if raw_folder:
        if not raw_folder.isdigit():
            raise Http404(_("Некоректна тека."))
        folder = get_object_or_404(Folder, pk=int(raw_folder), owner=request.user)

    book.folder = folder
    book.save(update_fields=["folder", "updated_at"])

    if folder:
        messages.success(request, _("«%(title)s» перенесено до теки «%(folder)s».") % {"title": book.title, "folder": folder.name})
        fallback = reverse("book_library:folder", args=[folder.pk])
    else:
        messages.success(request, _("«%(title)s» прибрано з теки.") % {"title": book.title})
        fallback = reverse("book_library:library")

    return redirect(_safe_next(request, fallback))
