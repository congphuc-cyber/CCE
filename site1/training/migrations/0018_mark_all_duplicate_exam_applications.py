from django.db import migrations
from django.db.models import Exists, OuterRef


def mark_all_duplicate_applications(apps, schema_editor):
    ExamApplication = apps.get_model("training", "ExamApplication")
    duplicate_siblings = ExamApplication.objects.filter(
        exam_period_id=OuterRef("exam_period_id"),
        id_number=OuterRef("id_number"),
    ).exclude(pk=OuterRef("pk"))
    duplicate_ids = (
        ExamApplication.objects
        .annotate(has_duplicate=Exists(duplicate_siblings))
        .filter(has_duplicate=True)
        .values("pk")
    )
    ExamApplication.objects.filter(pk__in=duplicate_ids).update(
        status="duplicate"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0017_alter_examapplication_status"),
    ]

    operations = [
        migrations.RunPython(
            mark_all_duplicate_applications,
            migrations.RunPython.noop,
        ),
    ]
