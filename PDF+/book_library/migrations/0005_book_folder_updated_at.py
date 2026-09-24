from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_library', '0004_book_file_uuid_upload_path'),
    ]

    operations = [
        migrations.AddField(
            model_name='book',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name='folder',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
