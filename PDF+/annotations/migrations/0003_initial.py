import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('annotations', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='layer',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='layers', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='annotation',
            name='layer',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='annotations', to='annotations.layer'),
        ),
        migrations.AddConstraint(
            model_name='layer',
            constraint=models.UniqueConstraint(fields=('book', 'owner'), name='unique_layer_per_book_owner'),
        ),
        migrations.AddIndex(
            model_name='annotation',
            index=models.Index(fields=['layer', 'page_number'], name='annotations_layer_i_ff98c7_idx'),
        ),
    ]
