ROLE_MENUS = {
    "Giáo vụ": {
        "key": "education",
        "items": [
            {"key": "dashboard", "label": "Bảng điều khiển", "url_name": "dashboard"},
            {"key": "courses", "label": "Chương trình đào tạo", "url_name": "admin:training_course_changelist"},
            {"key": "classes", "label": "Lớp học", "url_name": "admin:training_trainingclass_changelist"},
            {"key": "teachers", "label": "Giáo viên", "url_name": "admin:training_teacher_changelist"},
            {"key": "learners", "label": "Học viên", "url_name": "admin:training_learner_changelist"},
            {"key": "certificates", "label": "Chứng nhận", "url_name": "certificate-list"},
            {"key": "reports", "label": "Báo cáo", "url_name": "report-list"},
        ],
    },
    "Giáo viên": {
        "key": "teacher",
        "items": [
            {"key": "my-classes", "label": "Lớp của tôi", "url_name": "my-class-list"},
        ],
    },
    "Cán bộ thi": {
        "key": "exam",
        "items": [
            {"key": "exam-periods", "label": "Đợt thi", "url_name": "exam-period-list"},
            {"key": "exam-applications", "label": "Hồ sơ đăng ký dự thi", "url_name": "exam-application-list"},
            {"key": "exam-rooms", "label": "Phòng thi", "url_name": "exam-room-list"},
            {"key": "exam-scores", "label": "Điểm thi", "url_name": "exam-score-list"},
            {"key": "exam-certificates", "label": "Chứng chỉ", "url_name": "exam-certificate-list"},
            {"key": "mybk-export", "label": "Xuất dữ liệu MyBK", "url_name": "mybk-export"},
            {"key": "exam-statistics", "label": "Thống kê", "url_name": "exam-statistics"},
        ],
    },
    "Quản trị viên": {
        "key": "administrator",
        "items": [
            {"key": "accounts", "label": "Tài khoản", "url_name": "admin:auth_user_changelist"},
            {"key": "permissions", "label": "Phân quyền", "url_name": "role-permissions"},
            {"key": "audit-log", "label": "Nhật ký thao tác", "url_name": "audit-log-list"},
        ],
    },
}

ROLE_ORDER = ("Giáo vụ", "Giáo viên", "Cán bộ thi", "Quản trị viên")

MENU_SECTIONS = (
    ("dashboard", "Bảng điều khiển"),
    ("training", "Quản lý đào tạo"),
    ("teaching", "Giảng dạy"),
    ("exams", "Quản lý khảo thí"),
    ("system", "Quản lý hệ thống"),
    ("other", "Chức năng khác"),
)

FEATURE_SECTIONS = {
    "dashboard": "dashboard",
    "courses": "training",
    "classes": "training",
    "teachers": "training",
    "learners": "training",
    "certificates": "training",
    "reports": "training",
    "my-classes": "teaching",
    "exam-periods": "exams",
    "exam-applications": "exams",
    "exam-rooms": "exams",
    "exam-scores": "exams",
    "exam-certificates": "exams",
    "mybk-export": "exams",
    "exam-statistics": "exams",
    "accounts": "system",
    "permissions": "system",
    "audit-log": "system",
}

FEATURE_ICONS = {
    "dashboard": "dashboard",
    "courses": "auto_stories",
    "classes": "groups",
    "teachers": "person_celebrate",
    "learners": "badge",
    "certificates": "workspace_premium",
    "reports": "analytics",
    "my-classes": "co_present",
    "exam-periods": "event",
    "exam-applications": "assignment",
    "exam-rooms": "meeting_room",
    "exam-scores": "grading",
    "exam-certificates": "workspace_premium",
    "mybk-export": "upload_file",
    "exam-statistics": "query_stats",
    "accounts": "manage_accounts",
    "permissions": "shield_person",
    "audit-log": "history",
}


def roles_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return ()
    if getattr(user, "is_superuser", False):
        return ("Quản trị viên",)

    group_names = set(user.groups.values_list("name", flat=True))
    return tuple(role for role in ROLE_ORDER if role in group_names)


def role_for_user(user):
    return " · ".join(roles_for_user(user)) or None


def group_menu_items(items, include_empty=False):
    items_by_section = {key: [] for key, _ in MENU_SECTIONS}
    for item in items:
        section = FEATURE_SECTIONS.get(item["key"], "other")
        if section in items_by_section:
            items_by_section[section].append(item)

    return tuple(
        {"key": key, "title": title, "items": items_by_section[key]}
        for key, title in MENU_SECTIONS
        if items_by_section[key] or (include_empty and key != "other")
    )
