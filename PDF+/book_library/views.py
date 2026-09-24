from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from annotations.models import Annotation, Layer
from annotations.serializers import serialize_annotation
from sharing.models import GroupAccess, ShareLink

from .access import (
    catalog_folder_ids,
    catalog_root_folders,
    folder_subtree_ids,
    is_catalog_folder,
    visible_books,
    visible_folders,
)
from .forms import BookForm, FolderForm
from .models import Book, Folder


def _folder_crumbs(folder):
    chain = []
    node = folder
    while node is not None:
        chain.append(node)
        node = node.parent

    if any(node.is_catalog for node in chain):
        crumbs = [{"url": reverse("book_library:catalog"), "label": "Каталог"}]
    else:
        crumbs = [{"url": reverse("book_library:library"), "label": "Бібліотека"}]

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


def _search_titles(queryset, query):
    needle = query.casefold()
    return [book for book in queryset if needle in book.title.casefold()]


def _safe_next(request, fallback):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


def home_view(request):
    return render(request, "book_library/home.html")


@login_required
def library_view(request, pk=None):
    folder = None
    is_owner = True
    query = request.GET.get("q", "").strip()

    if pk is not None:
        folder = get_object_or_404(visible_folders(request.user, request.session), pk=pk)
        is_owner = folder.owner_id == request.user.pk
        folders = visible_folders(request.user, request.session).filter(parent=folder)
        books = visible_books(request.user, request.session).filter(folder=folder)
        search_scope = visible_books(request.user, request.session).filter(
            folder_id__in=folder_subtree_ids(folder.pk)
        )
    else:
        folders = Folder.objects.filter(owner=request.user, parent=None)
        books = Book.objects.filter(owner=request.user, folder=None)
        search_scope = Book.objects.filter(owner=request.user)

    if query:
        folders = Folder.objects.none()
        books = _search_titles(
            search_scope.select_related("folder__parent__parent").order_by("title"), query
        )

    return render(
        request,
        "book_library/library.html",
        {
            "folder": folder,
            "folders": folders,
            "books": books,
            "query": query,
            "is_owner": is_owner,
            "in_catalog": is_catalog_folder(folder) if folder else False,
            "crumbs": _folder_crumbs(folder) if folder else None,
            "page_title": folder.name if folder else "Моя бібліотека",
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
            "page_title": "Поділилися зі мною",
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
            "page_title": "Каталог",
            "page_subtitle": "Матеріали, доступні всім користувачам",
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
        messages.success(request, "Теку створено.")
        return redirect("book_library:folder", pk=folder.pk)

    return render(
        request,
        "book_library/folder_form.html",
        {
            "form": form,
            "page_title": "Нова тека",
            "form_title": "Нова тека",
            "cancel_url": reverse("book_library:library"),
        },
    )


@login_required
def folder_edit_view(request, pk):
    folder = get_object_or_404(Folder, pk=pk, owner=request.user)
    form = FolderForm(request.POST if request.method == "POST" else None, instance=folder, owner=request.user)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Теку оновлено.")
        return redirect("book_library:folder", pk=folder.pk)

    return render(
        request,
        "book_library/folder_form.html",
        {
            "form": form,
            "page_title": "Редагування теки",
            "form_title": f"Тека «{folder.name}»",
            "cancel_url": reverse("book_library:folder", args=[folder.pk]),
        },
    )


@login_required
def folder_delete_view(request, pk):
    folder = get_object_or_404(Folder, pk=pk, owner=request.user)
    if request.method == "POST":
        parent_pk = folder.parent_id
        folder.delete()
        messages.success(request, "Теку видалено.")
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
            "crumbs": _folder_crumbs(folder) + [{"url": "", "label": "Додати книги"}],
            "page_title": f"Додати книги до «{folder.name}»",
        },
    )


def book_detail_view(request, pk):
    book = get_object_or_404(visible_books(request.user, request.session), pk=pk)

    is_owner = request.user.is_authenticated and book.owner_id == request.user.pk
    layer = (
        Layer.objects.filter(book=book, owner=request.user).first()
        if request.user.is_authenticated
        else None
    )

    return render(
        request,
        "book_library/book_detail.html",
        {
            "book": book,
            "is_owner": is_owner,
            "annotation_count": Annotation.objects.filter(layer=layer).count() if layer else 0,
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

    return render(
        request,
        "book_library/book_reader.html",
        {
            "book": book,
            "annotations": annotations,
            "login_url": reverse("accounts:login"),
            "palette": Annotation.PALETTE,
            "annotations_data": [serialize_annotation(a) for a in annotations],
        },
    )


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
        messages.success(request, f"«{book.title}» завантажено: {book.page_count} с.")
        return redirect("book_library:book_detail", pk=book.pk)

    return render(
        request,
        "book_library/book_form.html",
        {
            "form": form,
            "page_title": "Завантаження PDF",
            "form_title": "Завантажити PDF",
            "form_hint": "Приймаються файли у форматі PDF.",
            "cancel_url": reverse("book_library:library"),
        },
    )


@login_required
def book_edit_view(request, pk):
    book = get_object_or_404(Book, pk=pk, owner=request.user)
    form = BookForm(request.POST if request.method == "POST" else None, request.FILES if request.method == "POST" else None, instance=book, owner=request.user)

    if request.method == "POST" and form.is_valid():
        book = form.save()
        messages.success(request, "Книгу оновлено.")
        return redirect("book_library:book_detail", pk=book.pk)

    return render(
        request,
        "book_library/book_form.html",
        {
            "form": form,
            "page_title": "Редагування книги",
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
        messages.success(request, "Книгу видалено.")
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
            raise Http404("Некоректна тека.")
        folder = get_object_or_404(Folder, pk=int(raw_folder), owner=request.user)

    book.folder = folder
    book.save(update_fields=["folder", "updated_at"])

    if folder:
        messages.success(request, f"«{book.title}» перенесено до теки «{folder.name}».")
        fallback = reverse("book_library:folder", args=[folder.pk])
    else:
        messages.success(request, f"«{book.title}» прибрано з теки.")
        fallback = reverse("book_library:library")

    return redirect(_safe_next(request, fallback))
