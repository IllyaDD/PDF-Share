import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedAnnotation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('annotation_id', models.PositiveBigIntegerField()),
                ('deleted_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deleted_annotations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'api_deletedannotation',
                'indexes': [models.Index(fields=['owner', 'deleted_at'], name='api_deleted_owner_i_ce1c05_idx')],
            },
        ),
    ]
