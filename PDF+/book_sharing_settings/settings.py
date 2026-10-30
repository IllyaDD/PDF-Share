from datetime import timedelta
from pathlib import Path

from decouple import Csv, config
from django.contrib.messages import constants as messages
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", default=False, cast=bool)

# На проді — домен сайту; порожній список при DEBUG=false дає 400 на кожен запит.
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
# Повні origin-и зі схемою: https://example.com
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

if not DEBUG:
    # nginx термінує TLS і передає схему заголовком. Без цього build_absolute_uri
    # збирає http://-адреси, і Google OAuth відхиляє redirect_uri.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Починати з години; 31536000 — лише коли HTTPS перевірено, бо браузер запам'ятає.
    SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=3600, cast=int)


INSTALLED_APPS = [
    'unfold.apps.BasicAppConfig',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.inlines',

    'book_sharing_settings.admin_site.AdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'crispy_forms',
    'crispy_bootstrap5',
    'storages',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',

    'users',
    'book_library',
    'annotations',
    'sharing',
    'api',
]

MIDDLEWARE = [
    # Першим: відповідає на /health/ до перевірки ALLOWED_HOSTS (див. health.py).
    'book_sharing_settings.health.HealthCheckMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Статика з процесу gunicorn: окремого nginx для /static/ нема.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'book_sharing_settings.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
                'sharing.context_processors.invitations',
            ],
        },
    },
]

WSGI_APPLICATION = 'book_sharing_settings.wsgi.application'

AUTH_USER_MODEL = 'users.User'


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("PG_DB"),
        "USER": config("PG_USER"),
        "PASSWORD": config("PG_PASSWORD"),
        "HOST": config("PG_HOST"),
        "PORT": config("PG_PORT"),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'uk'

# msgid у коді — українські, тож для 'uk' каталог не потрібен: перекладається
# лише в locale/en. Назви мов не позначені для перекладу навмисно — у перемикачі
# кожна мова підписана сама собою.
LANGUAGES = [
    ('uk', 'Українська'),
    ('en', 'English'),
]
LOCALE_PATHS = [BASE_DIR / 'locale']

# Зона показу й розбору введеного часу. У базі (timestamptz) час однаково лежить в UTC.
TIME_ZONE = 'Europe/Kyiv'

USE_I18N = True

USE_TZ = True


STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
# Сюди збирає collectstatic (у Docker — під час build); віддає WhiteNoise.
STATIC_ROOT = BASE_DIR / 'staticfiles'


MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

MAX_UPLOAD_SIZE = 50 * 1024 * 1024


USE_R2 = config("USE_R2", default=False, cast=bool)

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    # На проді — хешовані імена + gzip і кеш на рік. Manifest вимагає
    # collectstatic, тому з DEBUG=true (runserver, локальні тести) — звичайне сховище.
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

if USE_R2:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": config("R2_BUCKET"),
            "access_key": config("R2_ACCESS_KEY_ID"),
            "secret_key": config("R2_SECRET_ACCESS_KEY"),
            "endpoint_url": f"https://{config('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "signature_version": "s3v4",
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": config("R2_URL_EXPIRE", default=3600, cast=int),
            "file_overwrite": False,
        },
    }


LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'book_library:home'
LOGOUT_REDIRECT_URL = 'book_library:home'


CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'


MESSAGE_TAGS = {messages.ERROR: 'danger'}


GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_OAUTH_CLIENT_SECRET = config('GOOGLE_OAUTH_CLIENT_SECRET', default='')
# Client ID застосунків iOS/Android через кому — для api/v1/auth/google/.
GOOGLE_MOBILE_CLIENT_IDS = config('GOOGLE_MOBILE_CLIENT_IDS', default='', cast=Csv())
# Допустимі aud ID-токена. Web client ID теж: на Android (і на iOS із serverClientID)
# токен видається з aud = web client ID, а мобільний ID стоїть лише в azp.
GOOGLE_TOKEN_AUDIENCES = [
    client_id for client_id in [GOOGLE_OAUTH_CLIENT_ID, *GOOGLE_MOBILE_CLIENT_IDS] if client_id
]


# Django 6.1 налаштовує пошту через MAILERS; старі EMAIL_HOST/EMAIL_* застарілі,
# тому змінні оточення названі SMTP_*, а не EMAIL_*.
# Порожній SMTP_HOST = листи друкуються в консоль (локальна розробка).
SMTP_HOST = config('SMTP_HOST', default='')

if SMTP_HOST:
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'OPTIONS': {
                'host': SMTP_HOST,
                'port': config('SMTP_PORT', default=587, cast=int),
                'username': config('SMTP_USER', default=''),
                'password': config('SMTP_PASSWORD', default=''),
                'use_tls': config('SMTP_USE_TLS', default=True, cast=bool),
                'timeout': 10,
            },
        },
    }
else:
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.console.EmailBackend',
        },
    }

DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='PDF+ <noreply@localhost>')

# Посилання для скидання пароля діє добу (дефолт Django — три).
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24


ADMIN_URL = config("ADMIN_SITE_URL", default="admin").strip("/") or "admin"

UNFOLD = {
    "SITE_TITLE": "PDF Share",
    "SITE_HEADER": "PDF Share",
    "SITE_SYMBOL": "menu_book",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": "book_sharing_settings.dashboard.environment_callback",
    "DASHBOARD_CALLBACK": "book_sharing_settings.dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Overview",
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                    {
                        "title": "Users",
                        "icon": "people",
                        "link": reverse_lazy("admin:users_user_changelist"),
                    },
                ],
            },
            {
                "title": "Library",
                "separator": True,
                "items": [
                    {
                        "title": "Books",
                        "icon": "menu_book",
                        "link": reverse_lazy("admin:book_library_book_changelist"),
                    },
                    {
                        "title": "Folders",
                        "icon": "folder",
                        "link": reverse_lazy("admin:book_library_folder_changelist"),
                    },
                ],
            },
            {
                "title": "Sharing",
                "separator": True,
                "items": [
                    {
                        "title": "Groups",
                        "icon": "groups",
                        "link": reverse_lazy("admin:sharing_group_changelist"),
                    },
                    {
                        "title": "Share links",
                        "icon": "link",
                        "link": reverse_lazy("admin:sharing_sharelink_changelist"),
                    },
                ],
            },
        ],
    },
}


# Лічильники throttle мають бути спільні для воркерів gunicorn: LocMemCache — свій у кожному
# процесі, і ліміт множився б на WEB_CONCURRENCY. Порожній REDIS_URL — локально й у тестах.
REDIS_URL = config("REDIS_URL", default="")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                # Redis упав — вхід працює без ліміту, а не віддає 500 і не висне.
                "IGNORE_EXCEPTIONS": True,
                "SOCKET_CONNECT_TIMEOUT": 1,
                "SOCKET_TIMEOUT": 1,
            },
        },
    }
    # Проковтнуті помилки йдуть у лог рівня ERROR, а звідти — в Sentry.
    DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
else:
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "auth_token": "10/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    # Використаний при ротації refresh іде в blacklist: інакше старий токен жив би далі
    # поруч із новим, і вихід із пристрою (api/v1/auth/logout/) нічого б не відкликав.
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "UPDATE_LAST_LOGIN": False,
    # Токен несе хеш пароля: зміна чи скидання пароля відкликає всі видані токени.
    # Окремо від blacklist: access він не бачить, а хеш пароля перевіряється на кожен запит.
    "CHECK_REVOKE_TOKEN": True,
}

API_SYNC_OVERLAP_SECONDS = 5


SENTRY_DSN = config("SENTRY_DSN", default="")

if SENTRY_DSN:
    import logging

    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=config("SENTRY_ENVIRONMENT", default="production"),
        # Хеш коміту; deploy-скрипт передає `git rev-parse HEAD`.
        release=config("SENTRY_RELEASE", default=None),
        integrations=[
            DjangoIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=config("SENTRY_TRACES_SAMPLE_RATE", default=0.1, cast=float),
        send_default_pii=False,
    )
