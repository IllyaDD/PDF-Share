from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    email = models.EmailField("email address", unique=True)

    class Meta:
        db_table = "accounts_user"
