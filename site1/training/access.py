from training.models import RoleFeaturePermission
from training.navigation import ROLE_MENUS, roles_for_user


def all_features():
    features = {}
    for role in ROLE_MENUS.values():
        for item in role["items"]:
            features.setdefault(item["key"], item)
    return tuple(features.values())


def default_features_for_role(role_name):
    role = ROLE_MENUS.get(role_name)
    if not role:
        return frozenset()
    return frozenset(item["key"] for item in role["items"])


def enabled_features_for_user(user):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return frozenset()

    features = all_features()
    if getattr(user, "is_superuser", False):
        return frozenset(item["key"] for item in features)

    roles = roles_for_user(user)
    if not roles:
        return frozenset()

    overrides = {
        (role_name, feature): allowed
        for role_name, feature, allowed in RoleFeaturePermission.objects.filter(
            group__name__in=roles
        ).values_list("group__name", "feature", "allowed")
    }
    enabled = set()
    for role_name in roles:
        defaults = default_features_for_role(role_name)
        for item in features:
            feature = item["key"]
            if overrides.get((role_name, feature), feature in defaults):
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
            )
            for item in all_features()
        }
        for group in groups
    }
