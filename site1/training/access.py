from training.models import RoleFeaturePermission
from training.navigation import ROLE_MENUS, roles_for_user


REQUIRED_ADMIN_FEATURES = frozenset({"accounts", "permissions", "audit-log"})


def all_features():
    features = {}
    for role in ROLE_MENUS.values():
        for item in role["items"]:
            features.setdefault(item["key"], item)
    return tuple(features.values())


def default_features_for_role(role_name):
    if role_name == "Quản trị viên":
        return frozenset(item["key"] for item in all_features())

    role = ROLE_MENUS.get(role_name)
    if not role:
        return frozenset()
    return frozenset(item["key"] for item in role["items"])


def enabled_features_for_user(user):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return frozenset()

    features = all_features()
    roles = roles_for_user(user)
    if not roles:
        return frozenset()

    overrides = {
        (role_name, feature): (allowed, deny_overrides)
        for role_name, feature, allowed, deny_overrides in RoleFeaturePermission.objects.filter(
            group__name__in=roles
        ).values_list("group__name", "feature", "allowed", "deny_overrides")
    }
    denied_features = {
        feature
        for (_, feature), (_, deny_overrides) in overrides.items()
        if deny_overrides
    }
    enabled = set()
    for role_name in roles:
        defaults = default_features_for_role(role_name)
        for item in features:
            feature = item["key"]
            if (
                role_name == "Quản trị viên"
                and feature in REQUIRED_ADMIN_FEATURES
            ):
                enabled.add(feature)
                continue
            if feature in denied_features:
                continue
            allowed = overrides.get((role_name, feature), (feature in defaults, False))[0]
            if allowed:
                enabled.add(feature)
    return frozenset(enabled)


def user_has_feature(user, feature):
    return feature in enabled_features_for_user(user)


def role_feature_states(groups):
    role_names = tuple(group.name for group in groups)
    overrides = {
        (group_name, feature): allowed
        for group_name, feature, allowed in RoleFeaturePermission.objects.filter(
            group__name__in=role_names
        ).values_list("group__name", "feature", "allowed")
    }
    return {
        group.name: {
            item["key"]: overrides.get(
                (group.name, item["key"]),
                item["key"] in default_features_for_role(group.name),
            ) or (
                group.name == "Quản trị viên"
                and item["key"] in REQUIRED_ADMIN_FEATURES
            )
            for item in all_features()
        }
        for group in groups
    }
