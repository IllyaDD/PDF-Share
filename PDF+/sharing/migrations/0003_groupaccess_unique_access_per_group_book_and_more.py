from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_library', '0002_initial'),
        ('sharing', '0002_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='groupaccess',
            constraint=models.UniqueConstraint(fields=('group', 'book'), name='unique_access_per_group_book'),
        ),
        migrations.AddConstraint(
            model_name='groupaccess',
            constraint=models.UniqueConstraint(fields=('group', 'folder'), name='unique_access_per_group_folder'),
        ),
    ]
