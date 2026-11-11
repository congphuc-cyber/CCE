from training.access import all_features, enabled_features_for_user
from training.navigation import group_menu_items, roles_for_user


ROUTE_FEATURES = {
    "dashboard": "dashboard",
    "my-class-list": "my-classes",
    "class-detail": "classes",
    "class-close": "classes",
    "class-enroll": "classes",
    "class-assignments": "classes",
    "class-assignment-remove": "classes",
    "certificate-list": "certificates",
    "report-list": "reports",
    "exam-period-list": "exam-periods",
    "exam-application-list": "exam-applications",
    "exam-room-list": "exam-rooms",
    "exam-score-list": "exam-scores",
    "exam-certificate-list": "exam-certificates",
    "mybk-export": "mybk-export",
    "exam-statistics": "exam-statistics",
    "role-permissions": "permissions",
    "audit-log-list": "audit-log",
    "audit-log-detail": "audit-log",
}

ADMIN_ROUTE_FEATURES = {
    "training_course_": "courses",
    "training_trainingclass_": "classes",
    "training_teacher_": "teachers",
    "training_learner_": "learners",
    "auth_user_": "accounts",
}


def _active_menu(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None

    route_name = match.url_name or ""
    if route_name in ROUTE_FEATURES:
        return ROUTE_FEATURES[route_name]

    if "admin" in match.namespaces:
        for route_prefix, feature in ADMIN_ROUTE_FEATURES.items():
            if route_name.startswith(route_prefix):
                return feature

    return None


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
        "sidebar_groups": group_menu_items(
            items,
            include_empty=request.user.is_superuser or "Quản trị viên" in roles,
        ),
        "user_roles": roles,
        "active_menu": _active_menu(request),
    }
