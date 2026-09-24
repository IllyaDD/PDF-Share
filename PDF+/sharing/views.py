import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from book_library.access import remember_share_token

from .forms import GroupAccessForm, GroupForm, MembershipForm, ShareLinkForm
from .models import Group, GroupAccess, GroupMembership, ShareLink


@login_required
def group_list_view(request):
    return render(
        request,
        "sharing/group_list.html",
        {
            "owned_groups": Group.objects.filter(owner=request.user),
            "member_groups": Group.objects.filter(memberships__user=request.user).exclude(
                owner=request.user
            ),
            "create_url": reverse("sharing:group_create"),
            "page_title": "Групи",
        },
    )


@login_required
def group_detail_view(request, pk):
    group = get_object_or_404(Group, pk=pk)
    is_owner = group.owner_id == request.user.pk
    is_member = GroupMembership.objects.filter(group=group, user=request.user).exists()
    if not (is_owner or is_member):
        messages.error(request, "Немає доступу до цієї групи.")
        return redirect("sharing:group_list")

    form = MembershipForm(request.POST if request.method == "POST" else None, group=group) if is_owner else None
    if is_owner and request.method == "POST" and form.is_valid():
        GroupMembership.objects.create(group=group, user=form.cleaned_user)
        messages.success(request, "Учасника додано.")
        return redirect("sharing:group_detail", pk=group.pk)

    return render(
        request,
        "sharing/group_detail.html",
        {
            "group": group,
            "is_owner": is_owner,
            "memberships": GroupMembership.objects.filter(group=group).select_related("user"),
            "accesses": GroupAccess.objects.filter(group=group).select_related("book", "folder"),
            "membership_form": form,
            "crumbs": [
                {"url": reverse("sharing:group_list"), "label": "Групи"},
                {"url": "", "label": group.name},
            ],
            "page_title": group.name,
            "page_subtitle": f"Вчитель: {group.owner.username}",
        },
    )


@login_required
def group_create_view(request):
    form = GroupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.owner = request.user
        group.invite_token = secrets.token_urlsafe(32)
        group.save()
        messages.success(request, "Групу створено. Поділіться посиланням для приєднання з учнями.")
        return redirect("sharing:group_detail", pk=group.pk)

    return render(
        request,
        "sharing/group_form.html",
        {
            "form": form,
            "page_title": "Нова група",
            "form_title": "Нова група",
            "cancel_url": reverse("sharing:group_list"),
        },
    )


@login_required
def group_edit_view(request, pk):
    group = get_object_or_404(Group, pk=pk, owner=request.user)
    form = GroupForm(request.POST if request.method == "POST" else None, instance=group)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Групу оновлено.")
        return redirect("sharing:group_detail", pk=group.pk)

    return render(
        request,
        "sharing/group_form.html",
        {
            "form": form,
            "page_title": "Редагування групи",
            "form_title": f"Група «{group.name}»",
            "cancel_url": reverse("sharing:group_detail", args=[group.pk]),
        },
    )


@login_required
def group_delete_view(request, pk):
    group = get_object_or_404(Group, pk=pk, owner=request.user)
    if request.method == "POST":
        group.delete()
        messages.success(request, "Групу видалено.")
    return redirect("sharing:group_list")


@login_required
def group_invite_view(request, pk):
    group = get_object_or_404(Group, pk=pk, owner=request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "regenerate":
            group.regenerate_invite_token()
            messages.success(request, "Посилання для приєднання оновлено. Старе більше не діє.")
        elif action == "disable":
            group.invite_token = None
            group.save(update_fields=["invite_token"])
            messages.success(request, "Посилання для приєднання вимкнено.")
    return redirect("sharing:group_detail", pk=group.pk)


def group_join_view(request, token):
    group = Group.objects.filter(invite_token=token).select_related("owner").first()

    if group is None:
        return render(request, "sharing/group_join.html", {"group": None}, status=404)

    if not request.user.is_authenticated:
        return render(request, "sharing/group_join.html", {"group": group})

    if group.owner_id == request.user.pk:
        messages.info(request, "Ви власник цієї групи.")
        return redirect("sharing:group_detail", pk=group.pk)

    if GroupMembership.objects.filter(group=group, user=request.user).exists():
        messages.info(request, f"Ви вже в групі «{group.name}».")
        return redirect("sharing:group_detail", pk=group.pk)

    if request.method == "POST":
        GroupMembership.objects.create(group=group, user=request.user)
        messages.success(request, f"Ви приєдналися до групи «{group.name}».")
        return redirect("sharing:group_detail", pk=group.pk)

    return render(request, "sharing/group_join.html", {"group": group})


@login_required
def membership_remove_view(request, pk):
    membership = get_object_or_404(GroupMembership, pk=pk, group__owner=request.user)
    group_pk = membership.group_id
    if request.method == "POST":
        membership.delete()
        messages.success(request, "Учасника видалено з групи.")
    return redirect("sharing:group_detail", pk=group_pk)


@login_required
def access_create_view(request):
    form = GroupAccessForm(
        request.POST if request.method == "POST" else None, owner=request.user
    )
    if request.method == "GET":
        for field in ("group", "book", "folder"):
            if request.GET.get(field):
                form.initial[field] = request.GET[field]

    if request.method == "POST" and form.is_valid():
        access = form.save(commit=False)
        access.granted_by = request.user
        access.save()
        messages.success(request, "Доступ надано.")
        return redirect("sharing:group_detail", pk=access.group_id)

    return render(
        request,
        "sharing/access_form.html",
        {
            "form": form,
            "page_title": "Надати доступ",
            "form_title": "Надати доступ групі",
            "form_hint": "Оберіть групу і матеріал — книгу або теку — зі своєї бібліотеки.",
            "cancel_url": reverse("sharing:group_list"),
        },
    )


@login_required
def access_delete_view(request, pk):
    access = get_object_or_404(GroupAccess, pk=pk, group__owner=request.user)
    group_pk = access.group_id
    if request.method == "POST":
        access.delete()
        messages.success(request, "Доступ відкликано.")
    return redirect("sharing:group_detail", pk=group_pk)


@login_required
def link_list_view(request):
    return render(
        request,
        "sharing/link_list.html",
        {
            "links": ShareLink.objects.filter(created_by=request.user).select_related("book", "folder"),
            "create_url": reverse("sharing:link_create"),
            "page_title": "Посилання",
        },
    )


@login_required
def link_create_view(request):
    form = ShareLinkForm(request.POST if request.method == "POST" else None, owner=request.user)
    if request.method == "GET":
        for field in ("book", "folder"):
            if request.GET.get(field):
                form.initial[field] = request.GET[field]

    if request.method == "POST" and form.is_valid():
        link = form.save(commit=False)
        link.created_by = request.user
        link.token = secrets.token_urlsafe(32)
        link.save()
        messages.success(request, "Посилання створено.")
        return redirect("sharing:link_list")

    return render(
        request,
        "sharing/link_form.html",
        {
            "form": form,
            "page_title": "Нове посилання",
            "form_title": "Нове посилання",
            "form_hint": "Оберіть книгу або теку, до якої відкриваєте доступ.",
            "cancel_url": reverse("sharing:link_list"),
        },
    )


@login_required
def link_delete_view(request, pk):
    link = get_object_or_404(ShareLink, pk=pk, created_by=request.user)
    if request.method == "POST":
        link.delete()
        messages.success(request, "Посилання відкликано.")
    return redirect("sharing:link_list")


def share_view(request, token):
    link = ShareLink.objects.filter(token=token).select_related("book", "folder").first()

    error = None
    if link is None:
        error = "Посилання не існує або було відкликане."
    elif link.is_expired:
        error = "Термін дії посилання минув."
    else:
        remember_share_token(request.session, token)

    return render(request, "sharing/share_view.html", {"link": link, "error": error})
