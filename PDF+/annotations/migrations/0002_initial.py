import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('annotations', '0001_initial'),
        ('book_library', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='layer',
            name='book',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='layers', to='book_library.book'),
        ),
    ]
