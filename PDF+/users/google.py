from random import randint

from .models import User


class UnverifiedEmail(Exception):
    pass


def _unique_username(base):
    username = base
    while User.objects.filter(username=username).exists():
        username = f"{base}{randint(1, 9999)}"
    return username


def google_account(info):
    """Користувач за даними Google: userinfo вебу або claims ID-токена мобільного клієнта.

    Акаунт шукаємо за поштою, тож усі перевірки — тут, в одному місці: окремий шлях для
    API повторив би захоплення акаунта через непідтверджену пошту.
    Повертає (user, created, password_reset).
    """
    # «is not True», а не falsy-перевірка: рядок "false" теж був би істинним.
    if info.get("email_verified") is not True:
        raise UnverifiedEmail

    email = info["email"]
    base_name = info.get("given_name") or info.get("name") or email.split("@")[0]

    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "username": _unique_username(base_name),
            "first_name": info.get("given_name", ""),
            "last_name": info.get("family_name", ""),
            "email_verified": True,
        },
    )
    password_reset = False
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    elif not user.email_verified:
        # Адресу в акаунт записали без підтвердження — це міг зробити хтось інший, щоб
        # потім читати чужі книги й нотатки. Новий хеш пароля розлогінює всі інші сесії
        # (session auth hash) і JWT (CHECK_REVOKE_TOKEN).
        password_reset = user.has_usable_password()
        user.set_unusable_password()
        user.email_verified = True
        user.save(update_fields=["password", "email_verified"])
    return user, created, password_reset
