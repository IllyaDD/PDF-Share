import re
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from .models import User


class RegistrationTests(TestCase):
    def test_register_page_renders_crispy_form(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "form-control")
        self.assertContains(response, 'name="username"')
        self.assertNotContains(response, 'name="role"')

    def test_register_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "marichka",
                "email": "marichka@example.com",
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, reverse("book_library:welcome"))

        user = User.objects.get(username="marichka")
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertEqual(response.cookies["registration_success"].value, "1")

    def test_register_keeps_safe_next_instead_of_welcome(self):
        response = self.client.post(
            reverse("accounts:register") + "?next=" + reverse("book_library:catalog"),
            {
                "username": "taras",
                "email": "taras@example.com",
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, reverse("book_library:catalog"))

    def test_register_ignores_external_next(self):
        response = self.client.post(
            reverse("accounts:register") + "?next=https://evil.example.com/",
            {
                "username": "ostap",
                "email": "ostap@example.com",
                "password1": "spravzhniy-parol-42",
                "password2": "spravzhniy-parol-42",
            },
        )
        self.assertRedirects(response, reverse("book_library:welcome"))

    def test_duplicate_email_rejected(self):
        User.objects.create_user(
            username="pershiy", email="taken@example.com", password="parol-parol-1"
        )
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "drugiy",
                "email": "taken@example.com",
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

    def test_delete_profile_requires_post(self):
        self.client.force_login(self.user)

        self.client.get(reverse("accounts:delete_profile"))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

        self.client.post(reverse("accounts:delete_profile"))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="oksana", email="oksana@example.com", password="staryi-parol-42"
        )

    def _request_reset(self, email):
        return self.client.post(reverse("accounts:password_reset"), {"email": email})

    def _reset_path_from_mail(self):
        match = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body)
        return match.group(1)

    def test_login_page_links_to_reset(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, reverse("accounts:password_reset"))

    def test_reset_page_renders_crispy_form(self):
        response = self.client.get(reverse("accounts:password_reset"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')

    def test_known_email_sends_mail_with_link(self):
        response = self._request_reset("oksana@example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["oksana@example.com"])
        self.assertIn("PDF+", message.subject)
        self.assertIn("/accounts/password/reset/", message.body)

    def test_unknown_email_gives_same_response_without_mail(self):
        response = self._request_reset("nikhto@example.com")
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_google_user_without_password_gets_no_mail(self):
        google_user = User.objects.create_user(
            username="google", email="google@example.com"
        )
        google_user.set_unusable_password()
        google_user.save()

        self._request_reset("google@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_flow(self):
        self._request_reset("oksana@example.com")
        reset_path = self._reset_path_from_mail()

        # Токен з листа кладеться в сесію, URL підміняється на set-password.
        response = self.client.get(reset_path)
        self.assertEqual(response.status_code, 302)
        set_password_path = response.url
        self.assertIn("set-password", set_password_path)

        response = self.client.get(set_password_path)
        self.assertTrue(response.context["validlink"])

        response = self.client.post(
            set_password_path,
            {"new_password1": "novyi-parol-2026", "new_password2": "novyi-parol-2026"},
        )
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("novyi-parol-2026"))
        # Лист дійшов на цю адресу — пошту підтверджено.
        self.assertTrue(self.user.email_verified)

        # Повторно посилання не спрацьовує: хеш пароля змінився, токен недійсний.
        response = self.client.get(reset_path)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])

    def test_invalid_token_shows_error_page(self):
        response = self.client.get(
            reverse(
                "accounts:password_reset_confirm",
                kwargs={"uidb64": "MQ", "token": "nevirnyi-token"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["validlink"])
        self.assertContains(response, reverse("accounts:password_reset"))


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.password = "staryi-parol-42"
        self.user = User.objects.create_user(
            username="bohdan", email="bohdan@example.com", password=self.password
        )

    def test_requires_login(self):
        url = reverse("accounts:password_change")
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={url}")

    def test_wrong_old_password_keeps_password(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": "ne-toi-parol",
                "new_password1": "novyi-parol-2026",
                "new_password2": "novyi-parol-2026",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_change_password_keeps_session(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounts:password_change"),
            {
                "old_password": self.password,
                "new_password1": "novyi-parol-2026",
                "new_password2": "novyi-parol-2026",
            },
        )
        self.assertRedirects(response, reverse("accounts:profile"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("novyi-parol-2026"))
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_profile_hides_change_button_for_google_user(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.get(reverse("accounts:profile"))
        self.assertNotContains(response, reverse("accounts:password_change"))

    def test_profile_shows_change_button(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, reverse("accounts:password_change"))


@patch("users.views.oauth")
class GoogleCallbackTests(TestCase):
    """Акаунт шукається за поштою — перевірки проти входу в чужий акаунт."""

    def _callback(self, oauth, **userinfo):
        info = {"email": "zhertva@example.com", "email_verified": True, "given_name": "Zhertva"}
        info.update(userinfo)
        oauth.google.get.return_value.json.return_value = info
        return self.client.get(reverse("accounts:google_callback"))

    def test_unverified_google_email_rejected(self, oauth):
        response = self._callback(oauth, email_verified=False)
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertFalse(User.objects.filter(email="zhertva@example.com").exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_new_google_user_is_verified(self, oauth):
        self._callback(oauth)
        user = User.objects.get(email="zhertva@example.com")
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())

    def test_squatted_account_loses_password_and_sessions(self, oauth):
        # Хтось зареєструвався з чужою адресою і тримає відкриту сесію.
        squatter = User.objects.create_user(
            username="atakuvalnyk", email="zhertva@example.com", password="parol-ataky-42"
        )
        attacker = Client()
        attacker.force_login(squatter)

        self._callback(oauth)

        squatter.refresh_from_db()
        self.assertTrue(squatter.email_verified)
        self.assertFalse(squatter.has_usable_password())
        self.assertEqual(self.client.session["_auth_user_id"], str(squatter.pk))
        # Стара сесія атакувальника більше не автентифікована.
        response = attacker.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)

    def test_verified_account_keeps_password_and_sessions(self, oauth):
        user = User.objects.create_user(
            username="zhertva", email="zhertva@example.com", password="parol-parol-42",
            email_verified=True,
        )
        other_device = Client()
        other_device.force_login(user)

        self._callback(oauth)

        user.refresh_from_db()
        self.assertTrue(user.check_password("parol-parol-42"))
        self.assertEqual(other_device.get(reverse("accounts:profile")).status_code, 200)


class EmailVerifiedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="oksana", email="oksana@example.com", password="parol-parol-42",
            email_verified=True,
        )
        self.client.force_login(self.user)

    def _edit(self, email):
        return self.client.post(
            reverse("accounts:edit_profile"),
            {"username": "oksana", "first_name": "", "last_name": "", "email": email},
        )

    def test_changing_email_drops_verification(self):
        self._edit("insha@example.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "insha@example.com")
        self.assertFalse(self.user.email_verified)

    def test_same_email_keeps_verification(self):
        self._edit("oksana@example.com")
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
