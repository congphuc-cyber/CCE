from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from training.access import all_features, role_feature_states, user_has_feature
from training.audit import audit_values_for_display, record_audit, snapshot_instance
from training.forms import (
    ClassAssignmentForm,
    DashboardDateRangeForm,
    EnrollmentForm,
    ProfilePasswordChangeForm,
    VietnameseAuthenticationForm,
)
from training.models import AuditLog, ClassAssignment, Enrollment, Learner, RoleFeaturePermission, Teacher, TrainingClass
from training.navigation import ROLE_MENUS, group_menu_items, roles_for_user
from training.pagination import PAGE_SIZE


class DemoForm(forms.Form):
    full_name = forms.CharField(label="Họ và tên")
    email = forms.EmailField(label="Địa chỉ email")


class AuditLogFilterForm(forms.Form):
    action = forms.ChoiceField(
        label="Loại thao tác",
        required=False,
        choices=(("", "Tất cả thao tác"), *AuditLog.Action.choices),
    )
    actor = forms.ModelChoiceField(
        label="Người thực hiện",
        required=False,
        queryset=get_user_model().objects.none(),
        empty_label="Tất cả người thực hiện",
    )
    from_date = forms.DateField(
        label="Từ ngày",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    to_date = forms.DateField(
        label="Đến ngày",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        self.fields["actor"].queryset = user_model.objects.filter(
            audit_logs__isnull=False
        ).distinct().order_by("username")
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean(self):
        cleaned_data = super().clean()
        from_date = cleaned_data.get("from_date")
        to_date = cleaned_data.get("to_date")
        if from_date and to_date and from_date > to_date:
            self.add_error("to_date", "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu.")
        return cleaned_data


AUDIT_OBJECT_TYPE_LABELS = {
    "auth.Group": "Vai trò",
    "auth.User": "Tài khoản",
    "training.ClassAssignment": "Phân công giáo viên",
    "training.Course": "Khóa học",
    "training.Enrollment": "Ghi danh học viên",
    "training.Learner": "Học viên",
    "training.Teacher": "Giáo viên",
    "training.TrainingClass": "Lớp học",
    "home.ClassAssignment": "Phân công giáo viên",
    "home.Course": "Khóa học",
    "home.Enrollment": "Ghi danh học viên",
    "home.Learner": "Học viên",
    "home.Teacher": "Giáo viên",
    "home.TrainingClass": "LỜp học",
    "training.ExamPeriod": "Đợt thi",
    "training.ExamApplication": "Hồ sơ đăng ký dự thi",
    "training.ExamRoom": "Phòng thi",
    "training.ExamRoomAssignment": "Xếp phòng thi",
    "training.ExamScore": "Điểm thi",
    "training.MyBKExportLog": "Nhật ký xuất MyBK",
}


def get_home(request):
    if request.user.is_authenticated:
        if user_has_feature(request.user, "dashboard"):
            return redirect("dashboard")
        if user_has_feature(request.user, "my-classes"):
            return redirect("my-class-list")
        if request.user.is_superuser:
            return redirect("admin:index")
        return redirect("profile")
    return CCELoginView.as_view(template_name="training/login.html")(request)


def permission_denied(request, exception=None):
    response = render(request, 'training/403.html', status=403)
    return response


def _can_manage_classes(user):
    roles = roles_for_user(user)
    return user.is_superuser or (
        bool(set(roles) - {"Giáo viên"}) and user_has_feature(user, "classes")
    )


class CCELoginView(LoginView):
    authentication_form = VietnameseAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        if user_has_feature(self.request.user, "dashboard"):
            return reverse("dashboard")
        if user_has_feature(self.request.user, "my-classes"):
            return reverse("my-class-list")
        if self.request.user.is_superuser:
            return reverse("admin:index")
        if user_has_feature(self.request.user, "accounts"):
            return reverse("admin:auth_user_changelist")
        if user_has_feature(self.request.user, "permissions"):
            return reverse("role-permissions")
        if user_has_feature(self.request.user, "audit-log"):
            return reverse("audit-log-list")
        return super().get_success_url()


def education_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _can_manage_classes(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


@login_required
def dashboard(request):
    if not user_has_feature(request.user, "dashboard"):
        raise PermissionDenied

    date_form = DashboardDateRangeForm(
        request.GET if "from_date" in request.GET or "to_date" in request.GET else None
    )
    date_form.is_valid()
    stats = [
        {
            "label": "Tổng đăng ký trực tuyến",
            "value": "—",
            "note": "Chưa có nguồn dữ liệu đăng ký trực tuyến.",
        },
        {
            "label": "Lớp đang mở",
            "value": TrainingClass.objects.filter(status=TrainingClass.Status.OPEN).count(),
            "note": "Số lớp hiện có trạng thái đang mở.",
        },
        {
            "label": "Học viên",
            "value": Learner.objects.count(),
            "note": "Tổng số hồ sơ học viên.",
        },
        {
            "label": "Chứng nhận đã cấp",
            "value": "—",
            "note": "Chưa có dữ liệu chứng nhận.",
        },
    ]
    return render(
        request,
        "training/dashboard.html",
        {
            "active_menu": "dashboard",
            "page_title": "Bảng điều khiển",
            "page_description": "Tổng quan hoạt động đào tạo của Trung tâm.",
            "stats": stats,
            "date_form": date_form,
            "chart_id": "daily-registration-chart",
            "chart_title": "Đăng ký trực tuyến theo ngày",
            "chart_labels": [],
            "chart_values": [],
        },
    )


@login_required
def profile(request):
    form = ProfilePasswordChangeForm(
        user=request.user,
        data=request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Mật khẩu đã được cập nhật.")
        return redirect("profile")

    return render(
        request,
        "training/profile.html",
        {
            "active_menu": "profile",
            "page_title": "Hồ sơ cá nhân",
            "page_description": "Thông tin tài khoản và cập nhật mật khẩu.",
            "profile_role_label": " · ".join(roles_for_user(request.user))
            or "Chưa được phân quyền",
            "form": form,
        },
    )


@login_required
def my_class_list(request):
    roles = roles_for_user(request.user)
    if not user_has_feature(request.user, "my-classes"):
        raise PermissionDenied

    teacher = Teacher.objects.filter(user=request.user).first()
    classes = TrainingClass.objects.none()
    if teacher:
        classes = (
            TrainingClass.objects.filter(
                teacher_assignments__teacher=teacher,
                teacher_assignments__is_active=True,
            )
            .select_related("course")
            .distinct()
        )

    page_obj = Paginator(classes, PAGE_SIZE).get_page(request.GET.get("page"))
    total_assigned_count = classes.count()
    open_assigned_count = classes.filter(status=TrainingClass.Status.OPEN).count()
    total_students_count = Enrollment.objects.filter(training_class__in=classes).count()
    closed_assigned_count = classes.filter(status=TrainingClass.Status.CLOSED).count()

    rows = [
        {
            "id": item.pk,
            "name": item.name,
            "course": item.course.name,
            "schedule": item.schedule,
            "status": item.get_status_display(),
            "view_url": reverse("class-detail", args=(item.pk,)),
        }
        for item in page_obj
    ]
    return render(
        request,
        "training/my_class_list.html",
        {
            "active_menu": "my-classes",
            "page_title": "Lớp của tôi",
            "breadcrumbs": [{"label": "Lớp của tôi"}],
            "rows": rows,
            "page_obj": page_obj,
            "teacher": teacher,
            "total_assigned_count": total_assigned_count,
            "open_assigned_count": open_assigned_count,
            "total_students_count": total_students_count,
            "closed_assigned_count": closed_assigned_count,
            "columns": [
                {"key": "name", "label": "Tên lớp"},
                {"key": "course", "label": "Khóa học"},
                {"key": "schedule", "label": "Lịch học"},
                {"key": "status", "label": "Trạng thái"},
            ],
        },
    )


def _can_view_class(user, training_class):
    roles = roles_for_user(user)
    if _can_manage_classes(user):
        return True
    if "Giáo viên" not in roles or not user_has_feature(user, "my-classes"):
        return False
    return ClassAssignment.objects.filter(
        training_class=training_class,
        teacher__user=user,
        is_active=True,
    ).exists()


@login_required
def class_detail(request, class_id):
    training_class = get_object_or_404(
        TrainingClass.objects.select_related("course").prefetch_related(
            "teacher_assignments__teacher",
            "enrollments__learner",
        ),
        pk=class_id,
    )
    if not _can_view_class(request.user, training_class):
        raise PermissionDenied

    can_manage = _can_manage_classes(request.user)
    enrollments = training_class.enrollments.select_related("learner").order_by("learner__full_name")
    page_obj = Paginator(enrollments, PAGE_SIZE).get_page(request.GET.get("page"))
    learners = [
        {
            "id": enrollment.learner_id,
            "name": enrollment.learner.full_name,
            "phone": enrollment.learner.phone,
            "email": enrollment.learner.email,
        }
        for enrollment in page_obj
    ]
    assignments = Paginator(
        training_class.teacher_assignments.filter(is_active=True).select_related("teacher"),
        PAGE_SIZE,
    ).get_page(request.GET.get("teachers_page"))
    for assignment in assignments:
        assignment.modal_id = f"remove-assignment-{assignment.pk}"
        assignment.remove_url = reverse(
            "class-assignment-remove",
            args=(training_class.pk, assignment.pk),
        )
    can_enroll = (
        can_manage
        and training_class.status == TrainingClass.Status.OPEN
        and training_class.enrolled_count < training_class.max_students
    )
    return render(
        request,
        "training/class_detail.html",
        {
            "active_menu": "classes",
            "page_title": training_class.name,
            "breadcrumbs": [
                {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")},
                {"label": training_class.name},
            ],
            "training_class": training_class,
            "assignments": assignments,
            "teachers_page_obj": assignments,
            "learners": learners,
            "page_obj": page_obj,
            "columns": [
                {"key": "name", "label": "Họ và tên"},
                {"key": "phone", "label": "Số điện thoại"},
                {"key": "email", "label": "Địa chỉ email"},
            ],
            "can_manage": can_manage,
            "can_enroll": can_enroll,
            "class_change_url": reverse(
                "admin:training_trainingclass_change",
                args=(training_class.pk,),
            ),
            "assignment_url": reverse("class-assignments", args=(training_class.pk,)),
            "enrollment_url": reverse("class-enroll", args=(training_class.pk,)),
            "close_url": reverse("class-close", args=(training_class.pk,)),
        },
    )


@education_required
@require_POST
def close_class(request, class_id):
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Vui lòng nhập lý do đóng lớp.")
        return redirect("class-detail", class_id=class_id)

    with transaction.atomic():
        training_class = get_object_or_404(
            TrainingClass.objects.select_for_update(),
            pk=class_id,
        )
        if training_class.status == TrainingClass.Status.CLOSED:
            messages.info(request, "Lớp học đã được đóng trước đó.")
            return redirect("class-detail", class_id=class_id)

        old_values = snapshot_instance(training_class)
        training_class.status = TrainingClass.Status.CLOSED
        training_class.close_reason = reason
        training_class.save(update_fields=("status", "close_reason"))
        record_audit(
            request.user,
            AuditLog.Action.EDIT,
            training_class,
            old_values=old_values,
            new_values=snapshot_instance(training_class),
            reason=reason,
        )
    messages.success(request, "Lớp học đã được đóng và sẽ không nhận ghi danh thêm.")
    return redirect("class-detail", class_id=class_id)


@education_required
def enroll_learner(request, class_id):
    training_class = get_object_or_404(TrainingClass, pk=class_id)
    if request.method == "POST":
        with transaction.atomic():
            training_class = get_object_or_404(
                TrainingClass.objects.select_for_update(),
                pk=class_id,
            )
            form = EnrollmentForm(request.POST, training_class=training_class)
            if form.is_valid():
                enrollment = form.save()
                record_audit(
                    request.user,
                    AuditLog.Action.CREATE,
                    enrollment,
                    new_values=snapshot_instance(enrollment),
                )
                messages.success(request, "Học viên đã được ghi danh vào lớp.")
                return redirect("class-detail", class_id=class_id)
    else:
        form = EnrollmentForm(training_class=training_class)

    return render(
        request,
        "training/enrollment_form.html",
        {
            "active_menu": "classes",
            "page_title": "Ghi danh học viên",
            "breadcrumbs": [
                {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")},
                {"label": training_class.name, "url": reverse("class-detail", args=(class_id,))},
                {"label": "Ghi danh học viên"},
            ],
            "training_class": training_class,
            "form": form,
            "has_learners": Learner.objects.exists(),
        },
    )


@education_required
def class_assignments(request, class_id):
    training_class = get_object_or_404(TrainingClass, pk=class_id)
    form = ClassAssignmentForm(
        request.POST if request.method == "POST" else None,
        training_class=training_class,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            assignment = form.save()
            record_audit(
                request.user,
                AuditLog.Action.CREATE,
                assignment,
                new_values=snapshot_instance(assignment),
            )
        messages.success(request, "Đã phân công giáo viên cho lớp.")
        return redirect("class-assignments", class_id=class_id)

    assignments = Paginator(
        training_class.teacher_assignments.filter(is_active=True).select_related("teacher"),
        PAGE_SIZE,
    ).get_page(request.GET.get("page"))
    for assignment in assignments:
        assignment.modal_id = f"remove-assignment-{assignment.pk}"
        assignment.remove_url = reverse(
            "class-assignment-remove",
            args=(training_class.pk, assignment.pk),
        )
    return render(
        request,
        "training/class_assignments.html",
        {
            "active_menu": "classes",
            "page_title": "Phân công giáo viên",
            "breadcrumbs": [
                {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")},
                {"label": training_class.name, "url": reverse("class-detail", args=(class_id,))},
                {"label": "Phân công giáo viên"},
            ],
            "training_class": training_class,
            "form": form,
            "assignments": assignments,
            "page_obj": assignments,
        },
    )


@education_required
@require_POST
def remove_class_assignment(request, class_id, assignment_id):
    assignment = get_object_or_404(
        ClassAssignment,
        pk=assignment_id,
        training_class_id=class_id,
        is_active=True,
    )
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Vui lòng nhập lý do bỏ phân công.")
        return redirect("class-assignments", class_id=class_id)

    old_values = snapshot_instance(assignment)
    assignment.is_active = False
    assignment.removed_at = timezone.now()
    assignment.removal_reason = reason
    with transaction.atomic():
        assignment.save(update_fields=("is_active", "removed_at", "removal_reason"))
        record_audit(
            request.user,
            AuditLog.Action.EDIT,
            assignment,
            old_values=old_values,
            new_values=snapshot_instance(assignment),
            reason=reason,
        )
    messages.success(request, "Đã bỏ phân công giáo viên.")
    return redirect("class-assignments", class_id=class_id)


@login_required
def role_permissions(request):
    if not user_has_feature(request.user, "permissions"):
        raise PermissionDenied

    for role_name in ROLE_MENUS:
        Group.objects.get_or_create(name=role_name)
    groups = [Group.objects.get(name=role_name) for role_name in ROLE_MENUS]
    features = all_features()
    states = role_feature_states(groups)
    matrix_rows = [
        {
            "feature": feature,
            "roles": [
                {
                    "group": group,
                    "allowed": states[group.name][feature["key"]],
                    "input_value": f"{group.pk}:{feature['key']}",
                }
                for group in groups
            ],
        }
        for feature in features
    ]

    if request.method == "POST":
        selected = set(request.POST.getlist("permissions"))
        new_states = {
            group.name: {
                feature["key"]: f"{group.pk}:{feature['key']}" in selected
                for feature in features
            }
            for group in groups
        }
        with transaction.atomic():
            for group in groups:
                for feature in features:
                    RoleFeaturePermission.objects.update_or_create(
                        group=group,
                        feature=feature["key"],
                        defaults={"allowed": new_states[group.name][feature["key"]]},
                    )
            administrator_group = next(
                group for group in groups if group.name == "Quản trị viên"
            )
            changed_roles = [
                group.name for group in groups
                if states.get(group.name) != new_states.get(group.name)
            ]
            if changed_roles:
                audit_old = {r: states[r] for r in changed_roles}
                audit_new = {r: new_states[r] for r in changed_roles}
                label = f"Phân quyền vai trò: {', '.join(changed_roles)}" if len(changed_roles) <= 2 else "Ma trận phân quyền theo vai trò"
            else:
                audit_old = states
                audit_new = new_states
                label = "Ma trận phân quyền theo vai trò"

            record_audit(
                request.user,
                AuditLog.Action.EDIT,
                administrator_group,
                old_values=audit_old,
                new_values=audit_new,
                object_label=label,
            )
        messages.success(request, "Ma trận phân quyền đã được lưu.")
        states = new_states
        matrix_rows = [
            {
                "feature": feature,
                "roles": [
                    {
                        "group": group,
                        "allowed": states[group.name][feature["key"]],
                        "input_value": f"{group.pk}:{feature['key']}",
                    }
                    for group in groups
                ],
            }
            for feature in features
        ]

    total_roles_count = len(groups)
    total_features_count = len(features)
    active_permissions_count = sum(
        1 for r in matrix_rows for cell in r["roles"] if cell["allowed"]
    )
    total_slots = total_roles_count * total_features_count
    coverage_rate = f"{round((active_permissions_count / total_slots * 100), 1)}%" if total_slots > 0 else "100%"

    return render(
        request,
        "training/role_permissions.html",
        {
            "active_menu": "permissions",
            "page_title": "Phân quyền theo vai trò",
            "page_description": "Chọn các chức năng mà từng vai trò được phép truy cập.",
            "breadcrumbs": [{"label": "Phân quyền theo vai trò"}],
            "role_names": [group.name for group in groups],
            "matrix_rows": matrix_rows,
            "total_roles_count": total_roles_count,
            "total_features_count": total_features_count,
            "active_permissions_count": active_permissions_count,
            "coverage_rate": coverage_rate,
        },
    )


@login_required
def audit_log_list(request):
    if not user_has_feature(request.user, "audit-log"):
        raise PermissionDenied

    has_filters = any(
        key in request.GET for key in ("action", "actor", "from_date", "to_date")
    )
    form = AuditLogFilterForm(request.GET if has_filters else None)
    logs = AuditLog.objects.select_related("actor").all()
    if form.is_valid():
        action = form.cleaned_data.get("action")
        actor = form.cleaned_data.get("actor")
        from_date = form.cleaned_data.get("from_date")
        to_date = form.cleaned_data.get("to_date")
        if action:
            logs = logs.filter(action=action)
        if actor:
            logs = logs.filter(actor=actor)
        if from_date:
            logs = logs.filter(created_at__date__gte=from_date)
        if to_date:
            logs = logs.filter(created_at__date__lte=to_date)

    page_obj = Paginator(logs, PAGE_SIZE).get_page(request.GET.get("page"))
    total_logs_count = AuditLog.objects.count()
    create_actions_count = AuditLog.objects.filter(action=AuditLog.Action.CREATE).count()
    edit_actions_count = AuditLog.objects.filter(action=AuditLog.Action.EDIT).count()
    delete_actions_count = AuditLog.objects.filter(action=AuditLog.Action.DELETE).count()

    return render(
        request,
        "training/audit_log_list.html",
        {
            "active_menu": "audit-log",
            "page_title": "Nhật ký thao tác",
            "page_description": "Theo dõi các thay đổi đã thực hiện trong hệ thống.",
            "breadcrumbs": [{"label": "Nhật ký thao tác"}],
            "filter_form": form,
            "page_obj": page_obj,
            "total_logs_count": total_logs_count,
            "create_actions_count": create_actions_count,
            "edit_actions_count": edit_actions_count,
            "delete_actions_count": delete_actions_count,
        },
    )


@login_required
def audit_log_detail(request, log_id):
    if not user_has_feature(request.user, "audit-log"):
        raise PermissionDenied
    log_entry = get_object_or_404(AuditLog.objects.select_related("actor"), pk=log_id)

    old_vals = log_entry.old_values if isinstance(log_entry.old_values, dict) else {}
    new_vals = log_entry.new_values if isinstance(log_entry.new_values, dict) else {}

    # Check if this is a role permissions matrix
    role_names = [r for r in ROLE_MENUS if r in old_vals or r in new_vals]
    is_permission_matrix = bool(role_names)

    target_role = None
    changed_roles = []
    changed_features = set()

    if is_permission_matrix:
        changed_roles = [
            r for r in role_names
            if old_vals.get(r) != new_vals.get(r)
        ]
        requested_role = request.GET.get("role")
        if requested_role in role_names:
            target_role = requested_role
        elif changed_roles:
            target_role = changed_roles[0]
        elif role_names:
            target_role = role_names[0]

        if target_role:
            old_role_dict = old_vals.get(target_role, {}) if isinstance(old_vals.get(target_role), dict) else {}
            new_role_dict = new_vals.get(target_role, {}) if isinstance(new_vals.get(target_role), dict) else {}
            changed_features = {
                k for k in set(old_role_dict.keys()) | set(new_role_dict.keys())
                if old_role_dict.get(k) != new_role_dict.get(k)
            }

    show_all = request.GET.get("show_all") == "1"
    only_changed = not show_all

    changed_entry_keys = set()
    if not is_permission_matrix and log_entry.action == AuditLog.Action.EDIT:
        changed_entry_keys = {
            k for k in set(old_vals.keys()) | set(new_vals.keys())
            if old_vals.get(k) != new_vals.get(k)
        }

    old_display = audit_values_for_display(
        log_entry.old_values,
        log_entry.object_type,
        target_role=target_role,
        changed_features=changed_features,
        only_changed=only_changed,
        changed_entry_keys=changed_entry_keys,
    )
    new_display = audit_values_for_display(
        log_entry.new_values,
        log_entry.object_type,
        target_role=target_role,
        changed_features=changed_features,
        only_changed=only_changed,
        changed_entry_keys=changed_entry_keys,
    )

    return render(
        request,
        "training/audit_log_detail.html",
        {
            "active_menu": "audit-log",
            "page_title": "Chi tiết nhật ký thao tác",
            "breadcrumbs": [
                {"label": "Nhật ký thao tác", "url": reverse("audit-log-list")},
                {"label": "Chi tiết"},
            ],
            "log_entry": log_entry,
            "object_type_label": AUDIT_OBJECT_TYPE_LABELS.get(
                log_entry.object_type,
                log_entry.object_type,
            ),
            "is_permission_matrix": is_permission_matrix,
            "available_roles": role_names,
            "changed_roles": changed_roles,
            "active_role": target_role,
            "changed_features": changed_features,
            "changed_entry_keys": changed_entry_keys,
            "show_all": show_all,
            "old_values_display": old_display,
            "new_values_display": new_display,
        },
    )


def ui_demo(request):
    if not settings.DEBUG:
        return HttpResponseNotFound()

    requested_role = request.GET.get('role', 'Giáo vụ')
    role_name = requested_role if requested_role in ROLE_MENUS else 'Giáo vụ'
    invalid_form = DemoForm(data={'full_name': '', 'email': 'khong-phai-email'})
    invalid_form.is_valid()
    filters = [
        {
            'name': 'keyword',
            'label': 'Từ khóa',
            'placeholder': 'Nhập tên hoặc mã',
            'value': request.GET.get('keyword', ''),
        },
        {
            'name': 'status',
            'label': 'Trạng thái',
            'placeholder': 'Tất cả trạng thái',
            'value': request.GET.get('status', ''),
            'options': [
                {'value': 'open', 'label': 'Đang mở'},
                {'value': 'closed', 'label': 'Đã đóng'},
            ],
        },
        {
            'name': 'from_date',
            'label': 'Từ ngày',
            'type': 'date',
            'value': request.GET.get('from_date', ''),
        },
    ]
    demo_page_obj = Paginator(
        [
            {
                'id': 1,
                'code': 'CCE-2026-014',
                'name': 'Nguyễn Minh Anh',
                'status': 'Hợp lệ',
                'view_url': '#',
                'edit_url': '#',
                'delete_url': '#demo-delete',
            },
            {
                'id': 2,
                'code': 'CCE-2026-015',
                'name': 'Trần Gia Bảo',
                'status': 'Mới',
                'view_url': '#',
                'edit_url': '#',
                'delete_url': '#demo-delete',
            },
            {
                'id': 3,
                'code': 'CCE-2026-016',
                'name': 'Lê Hoàng Phúc',
                'status': 'Đã công bố',
                'view_url': '#',
                'edit_url': '#',
                'delete_url': '#demo-delete',
            },
        ],
        2,
    ).get_page(1)
    return render(
        request,
        'training/ui_demo.html',
        {
            'demo_role_label': role_name,
            'demo_sidebar_items': ROLE_MENUS[role_name]['items'],
            'demo_sidebar_groups': group_menu_items(ROLE_MENUS[role_name]['items']),
            'active_menu': ROLE_MENUS[role_name]['items'][0]['key'],
            'filter_fields': filters,
            'from_date': request.GET.get('from_date', ''),
            'to_date': request.GET.get('to_date', ''),
            'demo_columns': [
                {'key': 'code', 'label': 'Mã hồ sơ'},
                {'key': 'name', 'label': 'Họ và tên'},
                {'key': 'status', 'label': 'Trạng thái'},
            ],
            'demo_rows': demo_page_obj,
            'empty_rows': [],
            'demo_page_obj': demo_page_obj,
            'demo_allow_delete': True,
            'normal_form': DemoForm(),
            'invalid_form': invalid_form,
            'chart_id': 'demo-daily-chart',
            'chart_title': 'Số hồ sơ theo ngày',
            'chart_labels': [
                '01/11/2026',
                '02/11/2026',
                '03/11/2026',
                '04/11/2026',
                '05/11/2026',
                '06/11/2026',
                '07/11/2026',
            ],
            'chart_values': [12, 19, 14, 23, 18, 21, 16],
        },
    )

# Exam views (Pha 1) — imported from exam_views.py
from training.exam_views import (
    exam_period_list,
    exam_period_add,
    exam_period_edit,
    exam_period_toggle,
    exam_application_list,
    exam_application_import,
    exam_application_deduplicate,
)

# Exam stubs (Pha 2-4) — imported from exam_stubs.py
from training.exam_stubs import (
    exam_room_list,
    exam_room_assign,
    exam_score_list,
    exam_score_entry,
    exam_score_upload,
    exam_score_publish,
    mybk_export,
    exam_statistics,
    report_list,
    report_export_excel,
    certificate_list,
    exam_certificate_list,
)


def ui_demo(request):
    if not settings.DEBUG:
        return HttpResponseNotFound()

    requested_role = request.GET.get("role", "Giáo vụ")
    role_name = requested_role if requested_role in ROLE_MENUS else "Giáo vụ"
    invalid_form = DemoForm(data={"full_name": "", "email": "khong-phai-email"})
    invalid_form.is_valid()
    filters = [
        {
            "name": "keyword",
            "label": "Từ khóa",
            "placeholder": "Nhập tên hoặc mã",
            "value": request.GET.get("keyword", ""),
        },
        {
            "name": "status",
            "label": "Trạng thái",
            "placeholder": "Tất cả trạng thái",
            "value": request.GET.get("status", ""),
            "options": [
                {"value": "open", "label": "Đang mở"},
                {"value": "closed", "label": "Đã đóng"},
            ],
        },
        {
            "name": "from_date",
            "label": "Từ ngày",
            "type": "date",
            "value": request.GET.get("from_date", ""),
        },
    ]
    from django.core.paginator import Paginator as _Paginator
    demo_page_obj = _Paginator(
        [
            {
                "id": 1,
                "code": "CCE-2026-014",
                "name": "Nguyễn Minh Anh",
                "status": "Hợp lệ",
                "view_url": "#",
                "edit_url": "#",
                "delete_url": "#demo-delete",
            },
            {
                "id": 2,
                "code": "CCE-2026-015",
                "name": "Trần Gia Bảo",
                "status": "Mới",
                "view_url": "#",
                "edit_url": "#",
                "delete_url": "#demo-delete",
            },
            {
                "id": 3,
                "code": "CCE-2026-016",
                "name": "Lê Hoàng Phúc",
                "status": "Đã công bố",
                "view_url": "#",
                "edit_url": "#",
                "delete_url": "#demo-delete",
            },
        ],
        2,
    ).get_page(1)
    return render(
        request,
        "training/ui_demo.html",
        {
            "demo_role_label": role_name,
            "demo_sidebar_items": ROLE_MENUS[role_name]["items"],
            "demo_sidebar_groups": group_menu_items(ROLE_MENUS[role_name]["items"]),
            "active_menu": ROLE_MENUS[role_name]["items"][0]["key"],
            "filter_fields": filters,
            "from_date": request.GET.get("from_date", ""),
            "to_date": request.GET.get("to_date", ""),
            "demo_columns": [
                {"key": "code", "label": "Mã hồ sơ"},
                {"key": "name", "label": "Họ và tên"},
                {"key": "status", "label": "Trạng thái"},
            ],
            "demo_rows": demo_page_obj,
            "empty_rows": [],
            "demo_page_obj": demo_page_obj,
            "demo_allow_delete": True,
            "normal_form": DemoForm(),
            "invalid_form": invalid_form,
            "chart_id": "demo-daily-chart",
            "chart_title": "Số hồ sơ theo ngày",
            "chart_labels": [
                "01/11/2026", "02/11/2026", "03/11/2026", "04/11/2026",
                "05/11/2026", "06/11/2026", "07/11/2026",
            ],
            "chart_values": [12, 19, 14, 23, 18, 21, 16],
        },
    )
