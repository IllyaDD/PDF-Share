import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('book_library', '0002_initial'),
        ('sharing', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='owned_groups', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='groupaccess',
            name='book',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='group_accesses', to='book_library.book'),
        ),
        migrations.AddField(
            model_name='groupaccess',
            name='folder',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='group_accesses', to='book_library.folder'),
        ),
        migrations.AddField(
            model_name='groupaccess',
            name='granted_by',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='granted_accesses', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='groupaccess',
            name='group',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='accesses', to='sharing.group'),
        ),
        migrations.AddField(
            model_name='groupmembership',
            name='group',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='sharing.group'),
        ),
        migrations.AddField(
            model_name='groupmembership',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_memberships', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='sharelink',
            name='book',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to='book_library.book'),
        ),
        migrations.AddField(
            model_name='sharelink',
            name='created_by',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='sharelink',
            name='folder',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='share_links', to='book_library.folder'),
        ),
        migrations.AddConstraint(
            model_name='groupmembership',
            constraint=models.UniqueConstraint(fields=('group', 'user'), name='unique_membership_per_group_user'),
        ),
    ]
