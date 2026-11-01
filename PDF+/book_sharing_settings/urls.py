from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

urlpatterns = [
    path(f'{settings.ADMIN_URL}/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),
    path('jsi18n/', JavaScriptCatalog.as_view(), name='javascript-catalog'),
    path('accounts/', include('users.urls')),
    # Версія в шляху: встановлений застосунок не оновиш примусово, тож несумісні зміни
    # контракту йдуть у новий префікс, а старий живе, доки ним користуються.
    path('api/v1/', include('api.urls')),
    path('', include('sharing.urls')),
    path('', include('annotations.urls')),
    path('', include('book_library.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
