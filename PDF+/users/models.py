from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    email = models.EmailField("email address", unique=True)
    # Власність адреси доведена: Google (email_verified) або лист скидання пароля.
    # Реєстрація з паролем і зміна пошти в профілі цього не доводять — тому вхід через
    # Google в акаунт без підтвердження скидає пароль, а з ним сесії й JWT того, хто
    # міг записати чужу адресу (див. users.views.GoogleCallbackView).
    email_verified = models.BooleanField("пошту підтверджено", default=False)

    class Meta:
        db_table = "accounts_user"
