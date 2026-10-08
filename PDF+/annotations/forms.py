from django import forms

from sharing.models import Group


class LayerPublishForm(forms.Form):

    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label="Групи",
    )

    def __init__(self, *args, groups, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = groups
