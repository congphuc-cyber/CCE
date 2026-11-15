from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0013_alter_enrollment_learner"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolefeaturepermission",
            name="deny_overrides",
            field=models.BooleanField(default=False, verbose_name="Ưu tiên từ chối"),
        ),
    ]
