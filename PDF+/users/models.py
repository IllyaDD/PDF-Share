from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):

    class Role(models.TextChoices):
        TEACHER = "teacher", "Вчитель"
        STUDENT = "student", "Учень"

    email = models.EmailField("email address", unique=True)

    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.STUDENT,
    )

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"
