from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(f'{settings.ADMIN_URL}/', admin.site.urls),
    path('accounts/', include('users.urls')),
    path('api/', include('api.urls')),
    path('', include('sharing.urls')),
    path('', include('annotations.urls')),
    path('', include('book_library.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
