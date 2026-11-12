import json

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.core.serializers.json import DjangoJSONEncoder

from training.models import AuditLog
from training.navigation import ROLE_MENUS


AUDIT_FEATURE_LABELS = {
    item["key"]: item["label"]
    for role in ROLE_MENUS.values()
    for item in role["items"]
}

ACCOUNT_FIELD_LABELS = {
    "full_name": "Họ và tên",
    "username": "Tên đăng nhập",
    "email": "Email",
    "role": "Vai trò",
    "is_active": "Trạng thái",
}


def _audit_model(object_type):
    app_label, _, model_name = object_type.rpartition(".")
    for candidate in (app_label, "training"):
        if not candidate:
            continue
        try:
            return apps.get_model(candidate, model_name)
        except LookupError:
            continue
    return None


def _audit_field_label(object_type, key, model):
    model_name = object_type.rsplit(".", 1)[-1]
    if model_name == "User" and key in ACCOUNT_FIELD_LABELS:
        return ACCOUNT_FIELD_LABELS[key]
    if model_name == "Group" and key == "name":
        return "Tên vai trò"
    if key == "feature":
        return "Chức năng hệ thống"
    if key == "allowed":
        return "Quyền truy cập"
    if key == "id":
        return "Mã"

    if model:
        try:
            return str(model._meta.get_field(key).verbose_name)
        except FieldDoesNotExist:
            pass
    return key.replace("_", " ").capitalize()


def _audit_value_label(value, key, field=None):
    if value is None:
        return "—"
    if key == "feature" and isinstance(value, str):
        return AUDIT_FEATURE_LABELS.get(value, value.replace("-", " ").capitalize())
    if isinstance(value, bool):
        if key == "is_active":
            return "Đang hoạt động" if value else "Ngừng hoạt động"
        if key == "allowed":
            return "Được phép" if value else "Không được phép"
        return "Có" if value else "Không"
    if field and field.choices:
        choices = dict(field.flatchoices)
        if value in choices:
            return str(choices[value])
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return ", ".join(
            f"{key.replace('_', ' ').capitalize()}: {_audit_value_label(item, key)}"
            for key, item in value.items()
        )
    return str(value)


def _permission_state_display(value):
    if value is True:
        return {"label": "Được phép", "css_class": "is-allowed"}
    if value is False:
        return {"label": "Không được phép", "css_class": "is-denied"}
    return {"label": "—", "css_class": "is-unknown"}


def audit_values_for_display(
    values,
    object_type,
    target_role=None,
    changed_features=None,
    only_changed=False,
    changed_entry_keys=None,
):
    values = values if isinstance(values, dict) else {}
    model = _audit_model(object_type)
    role_names = [role for role in ROLE_MENUS if role in values]
    is_permission_matrix = bool(role_names) and all(
        isinstance(values[role], dict) for role in role_names
    ) and len(role_names) == len(values)

    if is_permission_matrix:
        # If target_role is specified, filter to only that role
        if target_role and target_role in role_names:
            display_roles = [target_role]
        else:
            display_roles = role_names

        changed_features = changed_features or set()
        rows = []
        for feature_key, label in AUDIT_FEATURE_LABELS.items():
            if not any(feature_key in values[role] for role in display_roles):
                continue
            is_changed = feature_key in changed_features
            if only_changed and changed_features and not is_changed:
                continue
            rows.append(
                {
                    "key": feature_key,
                    "label": label,
                    "is_changed": is_changed,
                    "values": [
                        _permission_state_display(values[role].get(feature_key))
                        for role in display_roles
                    ],
                }
            )
        return {
            "is_matrix": True,
            "roles": display_roles,
            "all_roles": role_names,
            "active_role": display_roles[0] if len(display_roles) == 1 else None,
            "rows": rows,
            "total_count": len(AUDIT_FEATURE_LABELS),
            "changed_count": len(changed_features),
            "only_changed": only_changed,
        }

    entries = []
    for key, value in values.items():
        if only_changed and changed_entry_keys and key not in changed_entry_keys:
            continue
        try:
            field = model._meta.get_field(key) if model else None
        except FieldDoesNotExist:
            field = None
        entries.append(
            {
                "label": _audit_field_label(object_type, key, model),
                "value": _audit_value_label(value, key, field),
            }
        )
    return {"is_matrix": False, "entries": entries}


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
