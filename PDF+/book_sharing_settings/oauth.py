"""Реєстрація OAuth-провайдерів (Authlib)."""

from authlib.integrations.django_client import OAuth
from django.conf import settings

oauth = OAuth()

oauth.register(
    name="google",
    client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
    client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
    # Метадані OIDC тягнуться з discovery-документа: endpoint'и та JWKS
    # не хардкодяться і переживають ротацію ключів на боці Google.
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
