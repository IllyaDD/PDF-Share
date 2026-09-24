import book_library.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_library', '0005_book_folder_updated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='book',
            name='cover',
            field=models.FileField(blank=True, upload_to=book_library.models.cover_upload_path),
        ),
    ]
