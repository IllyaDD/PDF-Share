from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.utils.translation import gettext_lazy as _

from .models import User


class RegistrationForm(UserCreationForm):

    email = forms.EmailField(
        label="Email",
        help_text=_("На цю адресу зіставляється акаунт при вході через Google."),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "username",
            "email",
            "password1",
            "password2",
            Submit("submit", _("Зареєструватися"), css_class="btn btn-primary w-100 mt-3"),
        )


class LoginForm(AuthenticationForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "username",
            "password",
            Submit("submit", _("Увійти"), css_class="btn btn-primary w-100 mt-3"),
        )


class ProfileForm(forms.ModelForm):

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "username",
            "first_name",
            "last_name",
            "email",
            Submit("submit", _("Зберегти"), css_class="btn btn-primary mt-3"),
        )


class ResetRequestForm(PasswordResetForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "email",
            Submit("submit", _("Надіслати посилання"), css_class="btn btn-primary w-100 mt-3"),
        )


class ResetSetPasswordForm(SetPasswordForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "new_password1",
            "new_password2",
            Submit("submit", _("Зберегти пароль"), css_class="btn btn-primary w-100 mt-3"),
        )


class ChangePasswordForm(PasswordChangeForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = "post"
        self.helper.layout = Layout(
            "old_password",
            "new_password1",
            "new_password2",
            Submit("submit", _("Змінити пароль"), css_class="btn btn-primary w-100 mt-3"),
        )
