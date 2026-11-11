import json

from django.core.serializers.json import DjangoJSONEncoder

from training.models import AuditLog


def snapshot_instance(instance):
    values = {}
    for field in instance._meta.concrete_fields:
        if field.name == "password":
            continue
        values[field.name] = field.value_from_object(instance)
    return json.loads(json.dumps(values, cls=DjangoJSONEncoder))


def snapshot_account(user):
    return {
        "full_name": user.get_full_name(),
        "username": user.get_username(),
        "email": user.email,
        "role": list(
            user.groups.filter(name__in=("Giáo vụ", "Giáo viên", "Cán bộ thi", "Quản trị viên"))
            .order_by("name")
            .values_list("name", flat=True)
        ),
        "is_active": user.is_active,
    }


def record_audit(
    actor,
    action,
    obj,
    old_values=None,
    new_values=None,
    reason="",
    object_label=None,
):
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        actor_username=actor.get_username() if getattr(actor, "is_authenticated", False) else "Hệ thống",
        action=action,
        object_type=obj._meta.label,
        object_id=str(obj.pk or ""),
        object_label=(object_label or str(obj))[:255],
        old_values=old_values or {},
        new_values=new_values or {},
        reason=reason,
    )
