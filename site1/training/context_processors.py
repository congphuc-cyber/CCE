from training.access import all_features, enabled_features_for_user
from training.navigation import ROLE_MENUS, roles_for_user


def ui_navigation(request):
    roles = roles_for_user(request.user)
    enabled_features = enabled_features_for_user(request.user)
    items = [
        item
        for item in all_features()
        if item["key"] in enabled_features
    ]

    return {
        "user_role_label": " · ".join(roles) or "Chưa được phân vai trò",
        "sidebar_items": items,
        "user_roles": roles,
    }
