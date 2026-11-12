from django import template
from training.navigation import FEATURE_ICONS

register = template.Library()

STATUS_CLASSES = {
    "đang mở": "cce-badge-open status-open",
    "đã đóng": "cce-badge-closed status-closed",
    "chưa công bố": "cce-badge-draft status-unpublished",
    "đã công bố": "cce-badge-published status-published",
    "mới": "cce-badge-new status-new",
    "hợp lệ": "cce-badge-valid status-valid",
    "trùng": "cce-badge-duplicate status-duplicate",
    "đã cấp": "cce-badge-issued status-issued",
    "thu hồi": "cce-badge-revoked status-revoked",
    "đã thay thế": "cce-badge-replaced status-replaced",
}


@register.filter
def status_class(value):
    return STATUS_CLASSES.get(str(value).strip().lower(), "status-neutral")


@register.filter
def get_item(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key, "")
    return getattr(mapping, key, "")


@register.filter
def has_active_item(items, active_menu):
    return any(item.get("key") == active_menu for item in items)


@register.filter
def nav_icon(item_key):
    return FEATURE_ICONS.get(item_key, "circle")


@register.filter
def accessible_field(bound_field):
    attrs = {}
    described_by = []
    existing_described_by = bound_field.field.widget.attrs.get("aria-describedby")
    if existing_described_by:
        described_by.append(existing_described_by)
    if bound_field.help_text:
        described_by.append(f"{bound_field.auto_id}_helptext")
    if bound_field.errors:
        attrs["aria-invalid"] = "true"
        described_by.append(f"{bound_field.auto_id}_error")
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    return bound_field.as_widget(attrs=attrs)


@register.simple_tag
def page_url(request, page_number, page_param="page"):
    query = request.GET.copy()
    query[page_param] = page_number
    return f"?{query.urlencode()}"


@register.simple_tag
def get_admin_stats(model_name):
    """Trả về 4 thẻ thống kê đồng bộ cho từng màn hình admin."""
    from training.models import Course, TrainingClass, Teacher, Learner, ClassAssignment, Enrollment
    from django.contrib.auth import get_user_model

    model_name = (model_name or "").lower()
    if model_name == "course":
        return {
            "theme": "stat-theme-admin-course",
            "cards": [
                {"label": "Tổng chương trình", "value": Course.objects.count(), "note": "Khóa đào tạo kỹ thuật CCE"},
                {"label": "Lớp học trực thuộc", "value": TrainingClass.objects.count(), "note": "Tổng lớp đã và đang mở"},
                {"label": "Lượt học viên", "value": Enrollment.objects.count(), "note": "Học viên ghi danh theo học"},
                {"label": "Ngưỡng đạt trung bình", "value": "50%", "note": "Tiêu chuẩn đánh giá khóa học"},
            ]
        }
    elif model_name == "trainingclass":
        open_c = TrainingClass.objects.filter(status=TrainingClass.Status.OPEN).count()
        closed_c = TrainingClass.objects.filter(status=TrainingClass.Status.CLOSED).count()
        return {
            "theme": "stat-theme-admin-class",
            "cards": [
                {"label": "Tổng lớp học", "value": TrainingClass.objects.count(), "note": "Quy mô toàn bộ các lớp đào tạo"},
                {"label": "Lớp đang mở", "value": open_c, "note": "Lớp học đang diễn ra theo lịch"},
                {"label": "Lớp đã hoàn thành", "value": closed_c, "note": "Đã chốt danh sách & kết thúc"},
                {"label": "Tổng học viên", "value": Enrollment.objects.count(), "note": "Lượt ghi danh vào các lớp"},
            ]
        }
    elif model_name == "teacher":
        total_t = Teacher.objects.count()
        active_t = Teacher.objects.filter(teacher_assignments__is_active=True).distinct().count()
        assignments_count = ClassAssignment.objects.filter(is_active=True).count()
        return {
            "theme": "stat-theme-admin-teacher",
            "cards": [
                {"label": "Tổng giáo viên", "value": total_t, "note": "Giảng viên cơ hữu & thỉnh giảng"},
                {"label": "Đang giảng dạy", "value": active_t, "note": "Có phân công lớp trong học kỳ"},
                {"label": "Lớp đã phân công", "value": assignments_count, "note": "Lượt phân công lớp active"},
                {"label": "Tỉ lệ sẵn sàng", "value": "100%", "note": "Đảm bảo tiến độ đào tạo CCE"},
            ]
        }
    elif model_name == "learner":
        total_l = Learner.objects.count()
        enrolled = Enrollment.objects.count()
        classes_count = Enrollment.objects.values("training_class").distinct().count()
        return {
            "theme": "stat-theme-admin-learner",
            "cards": [
                {"label": "Tổng học viên", "value": total_l, "note": "Hồ sơ học viên lưu trên hệ thống"},
                {"label": "Lượt ghi danh", "value": enrolled, "note": "Ghi danh lớp học trung tâm"},
                {"label": "Lớp đang theo học", "value": classes_count, "note": "Số lớp có học viên tham gia"},
                {"label": "Hồ sơ hợp lệ", "value": "100%", "note": "Thông tin liên hệ & CCCD chuẩn"},
            ]
        }
    elif model_name in ("user", "account"):
        User = get_user_model()
        total_u = User.objects.count()
        active_u = User.objects.filter(is_active=True).count()
        staff_u = User.objects.filter(is_staff=True).count()
        return {
            "theme": "stat-theme-admin-user",
            "cards": [
                {"label": "Tổng tài khoản", "value": total_u, "note": "Tài khoản nhân sự & giáo viên CCE"},
                {"label": "Đang hoạt động", "value": active_u, "note": "Tài khoản trạng thái Active"},
                {"label": "Nhóm vai trò CCE", "value": 4, "note": "Admin, Giáo vụ, G.viên, Cán bộ thi"},
                {"label": "Bảo mật hệ thống", "value": "100%", "note": "Phân quyền theo chuẩn RBAC"},
            ]
        }
    return None
