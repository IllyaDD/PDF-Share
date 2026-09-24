from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Annotation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('page_number', models.PositiveIntegerField()),
                ('type', models.CharField(choices=[('highlight', 'Виділення')], default='highlight', max_length=16)),
                ('coords', models.JSONField(help_text='Список прямокутників у PDF user space: [{x0, y0, x1, y1}, ...]')),
                ('selected_text', models.TextField(blank=True)),
                ('note_text', models.TextField(blank=True)),
                ('color', models.CharField(default='#FFEB3B', max_length=7)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'annotations_annotation',
                'ordering': ['page_number', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='Layer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'annotations_layer',
            },
        ),
    ]
