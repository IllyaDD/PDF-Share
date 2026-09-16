"""Кастомний AdminSite: Unfold + коректний редірект після логіну."""

from django.contrib.admin.apps import AdminConfig as BaseAdminConfig
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.urls import reverse
from unfold.sites import UnfoldAdminSite


class AdminSite(UnfoldAdminSite):
    def login(self, request, extra_context=None):
        # Шаблон логіну Unfold не рендерить приховане поле `next`, тому сторінка,
        # відкрита без ?next=, після входу вела б на LOGIN_REDIRECT_URL сайту.
        # Підставляємо індекс адмінки — LoginView читає `next` з request.GET.
        if REDIRECT_FIELD_NAME not in request.GET and REDIRECT_FIELD_NAME not in request.POST:
            request.GET = request.GET.copy()
            request.GET[REDIRECT_FIELD_NAME] = reverse("admin:index", current_app=self.name)
        return super().login(request, extra_context)


class AdminConfig(BaseAdminConfig):
    default_site = "book_sharing_settings.admin_site.AdminSite"
