import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from annotations.models import Annotation
from book_library.models import Book, Folder
from sharing.models import Group, GroupMembership, ShareLink
from users.models import User

CHART_DAYS = 30

ACTIVITY_SERIES = (
    ("Users", User.objects, "date_joined", "#9333ea"),
    ("Books", Book.objects, "uploaded_at", "#0ea5e9"),
    ("Annotations", Annotation.objects, "created_at", "#f59e0b"),
)


def environment_callback(request):
    if settings.DEBUG:
        return ["Development", "warning"]
    return ["Production", "danger"]


def _stats():
    group_owners = Group.objects.values("owner").distinct().count()
    group_members = GroupMembership.objects.values("user").distinct().count()
    active_links = ShareLink.objects.filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=timezone.now())
    ).count()

    return [
        {"title": "Users", "value": User.objects.count(), "icon": "person"},
        {"title": "Group owners", "value": group_owners, "icon": "school"},
        {"title": "Group members", "value": group_members, "icon": "group"},
        {"title": "Books", "value": Book.objects.count(), "icon": "menu_book"},
        {"title": "Folders", "value": Folder.objects.count(), "icon": "folder"},
        {"title": "Annotations", "value": Annotation.objects.count(), "icon": "ink_highlighter"},
        {"title": "Groups", "value": Group.objects.count(), "icon": "groups"},
        {"title": "Active share links", "value": active_links, "icon": "link"},
    ]


def _daily_counts(queryset, field, days):
    rows = (
        queryset.filter(**{f"{field}__date__gte": days[0]})
        .annotate(day=TruncDate(field))
        .values("day")
        .annotate(n=Count("id"))
    )
    by_day = {row["day"]: row["n"] for row in rows}
    return [by_day.get(day, 0) for day in days]


def _activity_chart():
    today = timezone.localdate()
    days = [today - timedelta(days=offset) for offset in range(CHART_DAYS - 1, -1, -1)]

    return json.dumps(
        {
            "labels": [day.strftime("%d.%m") for day in days],
            "datasets": [
                {
                    "label": label,
                    "data": _daily_counts(queryset.all(), field, days),
                    "borderColor": color,
                    "backgroundColor": color,
                }
                for label, queryset, field, color in ACTIVITY_SERIES
            ],
        }
    )


def _activity_legend():
    return [{"label": label, "color": color} for label, _, _, color in ACTIVITY_SERIES]


def dashboard_callback(request, context):
    context.update(
        {
            "stats": _stats(),
            "activity_chart": _activity_chart(),
            "activity_legend": _activity_legend(),
        }
    )
    return context
