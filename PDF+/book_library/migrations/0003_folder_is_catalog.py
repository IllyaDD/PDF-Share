from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('book_library', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='folder',
            name='is_catalog',
            field=models.BooleanField(default=False, help_text='Тека каталогу: разом з усім вкладеним доступна кожному користувачу.'),
        ),
    ]
