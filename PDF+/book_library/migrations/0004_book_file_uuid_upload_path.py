import book_library.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_library', '0003_folder_is_catalog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='book',
            name='file',
            field=models.FileField(upload_to=book_library.models.book_upload_path),
        ),
    ]
