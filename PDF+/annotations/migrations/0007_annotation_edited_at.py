import django.utils.timezone
from django.db import migrations, models
from django.db.models import F


def copy_updated_at(apps, schema_editor):
    # Для наявних нотаток найкраща оцінка часу правки — updated_at, а не момент міграції:
    # інакше всі вони виглядали б «новішими» за будь-яку офлайн-правку з пристрою.
    Annotation = apps.get_model("annotations", "Annotation")
    Annotation.objects.update(edited_at=F("updated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("annotations", "0006_annotation_client_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="annotation",
            name="edited_at",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False),
        ),
        migrations.RunPython(copy_updated_at, migrations.RunPython.noop),
    ]
