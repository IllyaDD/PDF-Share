from django.test import TestCase
from django.urls import reverse

from .models import User


class RegistrationTests(TestCase):
    def test_register_page_renders_crispy_form(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "form-control")
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="role"')

    def test_register_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "marichka",
                "email": "marichka@example.com",
                "role": User.Role.TEACHER,
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, reverse("book_library:home"))

        user = User.objects.get(username="marichka")
        self.assertEqual(user.role, User.Role.TEACHER)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertEqual(response.cookies["registration_success"].value, "1")

    def test_duplicate_email_rejected(self):
        User.objects.create_user(
            username="pershiy", email="taken@example.com", password="parol-parol-1"
        )
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "drugiy",
                "email": "taken@example.com",
                "role": User.Role.STUDENT,
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="drugiy").exists())


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.password = "spravzhniy-parol-42"
        self.user = User.objects.create_user(
            username="olena", email="olena@example.com", password=self.password
        )

    def test_login_with_username(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "olena", "password": self.password},
        )
        self.assertRedirects(response, reverse("book_library:home"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_logout_requires_post(self):
        self.client.force_login(self.user)

        self.client.get(reverse("accounts:logout"))
        self.assertIn("_auth_user_id", self.client.session)

        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="taras", email="taras@example.com", password="spravzhniy-parol-42"
        )

    def test_profile_requires_login(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_edit_profile_updates_fields(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounts:edit_profile"),
            {
                "username": "taras",
                "first_name": "Тарас",
                "last_name": "Шевченко",
                "email": "taras@example.com",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))

        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Тарас")

    def test_edit_profile_cannot_change_role(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("accounts:edit_profile"),
            {
                "username": "taras",
                "first_name": "",
                "last_name": "",
                "email": "taras@example.com",
                "role": User.Role.TEACHER,
            },
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.STUDENT)

    def test_delete_profile_requires_post(self):
        self.client.force_login(self.user)

        self.client.get(reverse("accounts:delete_profile"))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

        self.client.post(reverse("accounts:delete_profile"))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
