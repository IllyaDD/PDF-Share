from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sharing', '0003_groupaccess_unique_access_per_group_book_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='invite_token',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
