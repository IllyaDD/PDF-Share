from django import forms
from django.utils.translation import gettext_lazy as _

from sharing.models import Group


class LayerPublishForm(forms.Form):

    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label=_("Групи"),
    )

    def __init__(self, *args, groups, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = groups
