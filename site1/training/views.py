from datetime import timedelta
from decimal import Decimal
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
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from training.access import (
    REQUIRED_ADMIN_FEATURES,
    all_features,
    role_feature_states,
    user_has_feature,
)
from training.audit import audit_values_for_display, record_audit, snapshot_instance
from training.forms import (
    ClassAssignmentForm,
    DashboardDateRangeForm,
    EnrollmentForm,
    EnrollmentTransferForm,
    EnrollmentTransferHistoryFilterForm,
    ProfilePasswordChangeForm,
    VietnameseAuthenticationForm,
)
from training.models import (
    AuditLog,
    Certificate,
    CertificateHistory,
    ClassAssignment,
    Course,
    Enrollment,
    EnrollmentTransferHistory,
    EXAM_CERTIFICATE_MIN_SCORE,
    ExamApplication,
    ExamCertificate,
    ExamPeriod,
    FinalCourseResult,
    Learner,
    RoleFeaturePermission,
    Teacher,
    TrainingClass,
)
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
    "training.EnrollmentTransferHistory": "Chuyển lớp",
    "training.FinalCourseResult": "Kết quả cuối khóa",
    "training.Learner": "Học viên",
    "training.Teacher": "Giáo viên",
    "training.TrainingClass": "Lớp học",
    "home.ClassAssignment": "Phân công giáo viên",
    "home.Course": "Khóa học",
    "home.Enrollment": "Ghi danh học viên",
    "home.EnrollmentTransferHistory": "Chuyển lớp",
    "home.Learner": "Học viên",
    "home.Teacher": "Giáo viên",
    "home.TrainingClass": "LỜp học",
    "training.ExamPeriod": "Đợt thi",
    "training.ExamApplication": "Hồ sơ đăng ký dự thi",
    "training.ExamRoom": "Phòng thi",
    "training.ExamRoomAssignment": "Xếp phòng thi",
    "training.ExamScore": "Điểm thi",
    "training.ExamCertificate": "Chứng chỉ khảo thí",
    "training.ExamCertificateHistory": "Lịch sử chứng chỉ",
    "training.MyBKExportLog": "Nhật ký xuất MyBK",
}


def get_home(request):
    registration_periods = (
        ExamPeriod.objects.filter(
            status=ExamPeriod.Status.PUBLISHED,
            registration_deadline__gte=timezone.localdate(),
        )
        .annotate(candidate_count=Count("applications__id_number", distinct=True))
        .filter(candidate_count__lt=F("max_candidates"))
        .select_related("cert_type")
        .order_by("exam_date", "name")[:4]
    )
    return render(
        request,
        "training/home.html",
        {
            "registration_periods": registration_periods,
        },
    )


def permission_denied(request, exception=None):
    response = render(request, 'training/403.html', status=403)
    return response


def _can_manage_classes(user):
    roles = roles_for_user(user)
    return bool(set(roles) - {"Giáo viên"}) and user_has_feature(user, "classes")


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
        if user_has_feature(self.request.user, "accounts"):
            return reverse("admin:auth_user_changelist")
        if user_has_feature(self.request.user, "permissions"):
            return reverse("role-permissions")
        if user_has_feature(self.request.user, "audit-log"):
            return reverse("audit-log-list")
        for role_name in roles_for_user(self.request.user):
            for item in ROLE_MENUS.get(role_name, {}).get("items", ()):
                if user_has_feature(self.request.user, item["key"]):
                    return reverse(item["url_name"])
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

    today = timezone.localdate()
    default_from_date = today - timedelta(days=29)
    has_date_filters = "from_date" in request.GET or "to_date" in request.GET
    date_form = DashboardDateRangeForm(
        request.GET if has_date_filters else None,
        initial={"from_date": default_from_date, "to_date": today},
    )
    date_range_is_valid = date_form.is_valid() if date_form.is_bound else True
    online_applications = ExamApplication.objects.filter(
        source=ExamApplication.Source.ONLINE
    )
    chart_labels = []
    chart_values = []
    chart_datasets = []
    chart_has_data = False
    if date_range_is_valid:
        cleaned_dates = date_form.cleaned_data if date_form.is_bound else {}
        from_date = cleaned_dates.get("from_date") or default_from_date
        to_date = cleaned_dates.get("to_date") or today
        series = [
            {
                "label": "Hồ sơ đăng ký trực tuyến",
                "short_label": "Hồ sơ trực tuyến",
                "queryset": online_applications,
                "date_field": "registered_at",
                "color": "#173B57",
                "fill_color": "rgba(23, 59, 87, 0.10)",
            },
            {
                "label": "Lượt ghi danh lớp học",
                "short_label": "Ghi danh lớp",
                "queryset": Enrollment.objects.all(),
                "date_field": "created_at",
                "color": "#0F766E",
                "fill_color": "rgba(15, 118, 110, 0.10)",
            },
            {
                "label": "Chứng nhận đào tạo đã cấp",
                "short_label": "Chứng nhận cấp",
                "queryset": Certificate.objects.filter(
                    status=Certificate.Status.ISSUED
                ),
                "date_field": "issued_at",
                "color": "#A86512",
                "fill_color": "rgba(168, 101, 18, 0.10)",
            },
        ]
        series_counts = []
        chart_days = set()
        for item in series:
            date_field = item["date_field"]
            counts = dict(
                item["queryset"]
                .filter(**{f"{date_field}__date__range": (from_date, to_date)})
                .annotate(day=TruncDate(date_field))
                .values("day")
                .annotate(total=Count("pk"))
                .values_list("day", "total")
            )
            series_counts.append(counts)
            chart_days.update(counts)

        ordered_days = sorted(chart_days)
        chart_labels = [day.strftime("%d/%m/%Y") for day in ordered_days]
        chart_datasets = [
            {
                "label": item["label"],
                "data": [counts.get(day, 0) for day in ordered_days],
                "borderColor": item["color"],
                "backgroundColor": item["fill_color"],
                "fillColor": item["fill_color"],
                "shortLabel": item["short_label"],
            }
            for item, counts in zip(series, series_counts)
        ]
        if chart_datasets:
            chart_values = chart_datasets[0]["data"]
        chart_has_data = bool(chart_labels)

    def choice_chart(
        chart_id,
        title,
        queryset,
        value_field,
        choices,
        chart_type,
        colors,
        empty_message,
        index_axis=None,
    ):
        counts = dict(
            queryset.values(value_field)
            .annotate(total=Count("pk"))
            .values_list(value_field, "total")
        )
        choice_values = {value for value, _ in choices}
        chart_labels = [label for _, label in choices]
        chart_values = [counts.get(value, 0) for value, _ in choices]
        unknown_statuses = sorted(set(counts) - choice_values)
        chart_labels.extend(unknown_statuses)
        chart_values.extend(counts[status] for status in unknown_statuses)
        chart_colors = [
            colors[index % len(colors)]
            for index in range(len(chart_values))
        ]
        return {
            "id": chart_id,
            "title": title,
            "type": chart_type,
            "labels": chart_labels,
            "values": chart_values,
            "colors": chart_colors,
            "index_axis": index_axis,
            "has_data": any(chart_values),
            "empty_message": empty_message,
        }

    dashboard_charts = [
        choice_chart(
            "exam-application-status-chart",
            "Hồ sơ dự thi theo trạng thái",
            ExamApplication.objects.all(),
            "status",
            ExamApplication.Status.choices,
            "bar",
            ["#3979AD", "#179B82", "#D95D63"],
            "Chưa có hồ sơ dự thi.",
        ),
        choice_chart(
            "exam-application-source-chart",
            "Hồ sơ dự thi theo nguồn",
            ExamApplication.objects.all(),
            "source",
            ExamApplication.Source.choices,
            "doughnut",
            ["#3979AD", "#E6A440", "#35A18B", "#8192A6"],
            "Chưa có nguồn hồ sơ để thống kê.",
        ),
        choice_chart(
            "training-class-status-chart",
            "Lớp học theo trạng thái",
            TrainingClass.objects.all(),
            "status",
            TrainingClass.Status.choices,
            "bar",
            ["#179B82", "#8192A6"],
            "Chưa có lớp học để thống kê.",
            index_axis="y",
        ),
        choice_chart(
            "training-class-format-chart",
            "Lớp học theo hình thức",
            TrainingClass.objects.all(),
            "learning_format",
            TrainingClass.LearningFormat.choices,
            "doughnut",
            ["#3979AD", "#28A9A1"],
            "Chưa có hình thức lớp học để thống kê.",
        ),
        choice_chart(
            "training-certificate-status-chart",
            "Chứng nhận đào tạo theo trạng thái",
            Certificate.objects.all(),
            "status",
            Certificate.Status.choices,
            "bar",
            ["#179B82", "#D95D63", "#E6A440"],
            "Chưa có chứng nhận đào tạo.",
            index_axis="y",
        ),
        choice_chart(
            "exam-period-status-chart",
            "Đợt thi theo trạng thái",
            ExamPeriod.objects.all(),
            "status",
            ExamPeriod.Status.choices,
            "bar",
            ["#E6A440", "#3979AD", "#8192A6"],
            "Chưa có đợt thi.",
            index_axis="y",
        ),
    ]
    dashboard_has_chart_data = chart_has_data or any(
        chart["has_data"] for chart in dashboard_charts
    )

    stats = [
        {
            "label": "Tổng đăng ký trực tuyến",
            "value": online_applications.count(),
            "note": "Tổng hồ sơ đăng ký dự thi trực tuyến.",
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
            "value": Certificate.objects.filter(
                status=Certificate.Status.ISSUED
            ).count(),
            "note": "Số chứng nhận đào tạo còn hiệu lực.",
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
            "chart_title": "Đăng ký, ghi danh và cấp chứng nhận theo ngày",
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "chart_datasets": chart_datasets,
            "chart_has_data": chart_has_data,
            "chart_has_multiple_series": True,
            "chart_data": {"labels": chart_labels, "datasets": chart_datasets},
            "dashboard_charts": dashboard_charts,
            "dashboard_has_chart_data": dashboard_has_chart_data,
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


def _generate_certificate_number():
    year = timezone.now().year
    prefix = f"CCE-{year}-"
    latest = Certificate.objects.filter(certificate_number__startswith=prefix).order_by("-certificate_number").first()
    if latest:
        suffix = latest.certificate_number.rsplit("-", 1)[-1]
        seq = int(suffix) + 1 if suffix.isdigit() else 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


class CertificateIssueForm(forms.Form):
    training_class = forms.ModelChoiceField(
        label="Lớp học",
        queryset=TrainingClass.objects.none(),
        empty_label="Chọn lớp",
    )
    learner = forms.ModelChoiceField(
        label="Học viên",
        queryset=Learner.objects.none(),
        empty_label="Chọn học viên",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["training_class"].queryset = TrainingClass.objects.select_related("course").order_by("-name")
        class_id = self.data.get("training_class") or self.initial.get("training_class")
        if class_id:
            self.fields["learner"].queryset = Learner.objects.filter(
                Q(final_course_results__training_class_id=class_id)
                | Q(enrollments__training_class_id=class_id)
            ).distinct().order_by("full_name")
        else:
            self.fields["learner"].queryset = Learner.objects.none()
        for field in self.fields.values():
            field.widget.attrs["class"] = "cce-select"

    def clean(self):
        cleaned = super().clean()
        training_class = cleaned.get("training_class")
        learner = cleaned.get("learner")
        if training_class and learner:
            if not training_class.final_results_locked:
                raise forms.ValidationError("Bảng điểm cuối khóa chưa được chốt.")
            result = FinalCourseResult.objects.filter(training_class=training_class, learner=learner).first()
            if result is None:
                raise forms.ValidationError("Học viên chưa có kết quả cuối khóa trong lớp này.")
            if (
                result.is_absent
                or result.score is None
                or result.score < training_class.course.pass_threshold
            ):
                raise forms.ValidationError("Học viên chưa đạt điều kiện cấp chứng nhận.")
            if Certificate.objects.filter(training_class=training_class, learner=learner, status=Certificate.Status.ISSUED).exists():
                raise forms.ValidationError("Học viên này đã có chứng nhận còn hiệu lực trong lớp.")
        return cleaned


CERTIFICATE_ISSUE_REASON = "Đã hoàn thành khóa học và đủ điều kiện để cấp chứng nhận."


def _issue_certificate_for_learner(request, training_class, learner):
    with transaction.atomic():
        certificate = Certificate.objects.create(
            training_class=training_class,
            learner=learner,
            certificate_number=_generate_certificate_number(),
            status=Certificate.Status.ISSUED,
            issued_by=request.user,
            reason=CERTIFICATE_ISSUE_REASON,
        )
        record_audit(
            request.user,
            AuditLog.Action.ISSUE,
            certificate,
            old_values={},
            new_values={
                "training_class_id": training_class.pk,
                "learner_id": learner.pk,
                "certificate_number": certificate.certificate_number,
                "status": certificate.status,
            },
            reason=CERTIFICATE_ISSUE_REASON,
            object_label=f"{certificate.certificate_number} — {learner.full_name}",
        )
        CertificateHistory.objects.create(
            certificate=certificate,
            old_status=Certificate.Status.ISSUED,
            new_status=Certificate.Status.ISSUED,
            reason=CERTIFICATE_ISSUE_REASON,
            changed_by=request.user,
        )
    return certificate


class CertificateStatusForm(forms.Form):
    status = forms.ChoiceField(label="Trạng thái mới", choices=Certificate.Status.choices)
    reason = forms.CharField(label="Lý do", widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}), required=False)

    def clean(self):
        cleaned = super().clean()
        reason = (cleaned.get("reason") or "").strip()
        if not reason:
            self.add_error("reason", "Vui lòng nhập lý do bắt buộc.")
        return cleaned


@login_required
def certificate_list(request):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    issue_requested = request.method == "POST" and (
        "issue_candidate" in request.POST or "issue_certificate" in request.POST
    )
    issue_form = CertificateIssueForm(request.POST if issue_requested else None)
    if issue_requested:
        if issue_form.is_valid():
            training_class = issue_form.cleaned_data["training_class"]
            learner = issue_form.cleaned_data["learner"]
            certificate = _issue_certificate_for_learner(request, training_class, learner)
            messages.success(request, f"Đã cấp chứng nhận {certificate.certificate_number} cho {learner.full_name}.")
            return redirect(f"{reverse('certificate-list')}?{request.GET.urlencode()}")
        errors = "; ".join(
            error
            for field_errors in issue_form.errors.values()
            for error in field_errors
        )
        messages.error(request, f"Không thể cấp chứng nhận: {errors}")

    results = FinalCourseResult.objects.select_related("training_class__course", "learner")
    enrollments = Enrollment.objects.filter(
        training_class__status=TrainingClass.Status.OPEN
    ).select_related("training_class__course", "learner")
    keyword = (request.GET.get("keyword") or "").strip()
    course_id = request.GET.get("course_id")
    class_id = request.GET.get("class_id")
    status = request.GET.get("status")
    if course_id:
        results = results.filter(training_class__course_id=course_id)
        enrollments = enrollments.filter(training_class__course_id=course_id)
    if class_id:
        results = results.filter(training_class_id=class_id)
        enrollments = enrollments.filter(training_class_id=class_id)

    candidates = {}
    for result in results.order_by("-updated_at", "-pk"):
        key = (result.training_class_id, result.learner_id)
        candidates.setdefault(
            key,
            {
                "result": result,
                "learner": result.learner,
                "training_class": result.training_class,
                "sort_date": result.updated_at,
                "sort_id": result.pk,
            },
        )
    for enrollment in enrollments.order_by("-created_at", "-pk"):
        key = (enrollment.training_class_id, enrollment.learner_id)
        candidates.setdefault(
            key,
            {
                "result": None,
                "learner": enrollment.learner,
                "training_class": enrollment.training_class,
                "sort_date": enrollment.created_at,
                "sort_id": enrollment.pk,
            },
        )

    certificates = Certificate.objects.select_related(
        "issued_by", "training_class__course", "learner"
    ).prefetch_related(
        "history__changed_by__groups",
        "replaced_certificate",
    ).order_by("-issued_at", "-pk")
    if course_id:
        certificates = certificates.filter(training_class__course_id=course_id)
    if class_id:
        certificates = certificates.filter(training_class_id=class_id)
    certificates_by_pair = {}
    for certificate in certificates:
        key = (certificate.training_class_id, certificate.learner_id)
        if key not in candidates:
            result = FinalCourseResult.objects.filter(
                training_class_id=certificate.training_class_id,
                learner_id=certificate.learner_id,
            ).select_related("training_class__course", "learner").first()
            candidates[key] = {
                "result": result,
                "learner": certificate.learner,
                "training_class": certificate.training_class,
                "sort_date": certificate.issued_at,
                "sort_id": certificate.pk,
            }
        certificates_by_pair.setdefault(key, []).append(certificate)

    rows = []
    for pair, candidate in candidates.items():
        candidate_certificates = certificates_by_pair.get(pair, [])
        if candidate_certificates:
            for certificate in candidate_certificates:
                if status and status != certificate.status:
                    continue
                if keyword and not any(
                    keyword.casefold() in value.casefold()
                    for value in (
                        candidate["learner"].full_name,
                        candidate["training_class"].name,
                        certificate.certificate_number,
                    )
                ):
                    continue
                rows.append({
                    **candidate,
                    "certificate": certificate,
                    "status": certificate.status,
                    "sort_date": certificate.issued_at,
                    "sort_id": certificate.pk,
                })
            continue

        row_status = "pending"
        if status and status != row_status:
            continue
        if keyword and not any(
            keyword.casefold() in value.casefold()
            for value in (
                candidate["learner"].full_name,
                candidate["training_class"].name,
            )
        ):
            continue
        rows.append({
            **candidate,
            "certificate": None,
            "status": row_status,
        })

    rows.sort(
        key=lambda row: (row["sort_date"], row["sort_id"]),
        reverse=True,
    )
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))

    courses = Course.objects.order_by("name")
    classes = TrainingClass.objects.select_related("course").order_by("name")
    if course_id:
        classes = classes.filter(course_id=course_id)

    return render(
        request,
        "training/certificate_list.html",
        {
            "active_menu": "certificates",
            "page_title": "Danh sách chứng nhận",
            "page_description": "Quản lý cấp, thu hồi và thay thế chứng nhận cho học viên đủ điều kiện.",
            "breadcrumbs": [{"label": "Chứng nhận"}],
            "page_obj": page_obj,
            "status_choices": (
                ("pending", "Chờ cấp"),
                *Certificate.Status.choices,
            ),
            "certificate_status_choices": Certificate.Status.choices,
            "courses": courses,
            "classes": classes,
            "keyword": keyword,
            "course_id": course_id,
            "class_id": class_id,
            "status": status,
            "has_active_filters": bool(keyword or course_id or class_id or status),
            "total_eligible": len(rows),
            "total_issued": Certificate.objects.filter(status=Certificate.Status.ISSUED).count(),
        },
    )


@login_required
def certificate_issue(request, result_id=None):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    candidate_result = None
    initial = {}
    if result_id is not None:
        candidate_result = get_object_or_404(
            FinalCourseResult.objects.select_related("training_class", "learner"),
            pk=result_id,
        )
        initial = {
            "training_class": candidate_result.training_class_id,
            "learner": candidate_result.learner_id,
        }
    if request.method == "GET":
        initial = {
            "training_class": request.GET.get("training_class") or initial.get("training_class"),
            "learner": request.GET.get("learner") or initial.get("learner"),
        }

    issue_form = CertificateIssueForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    if request.method == "POST" and issue_form.is_valid():
        training_class = issue_form.cleaned_data["training_class"]
        learner = issue_form.cleaned_data["learner"]
        certificate = _issue_certificate_for_learner(request, training_class, learner)
        messages.success(
            request,
            f"Đã cấp chứng nhận {certificate.certificate_number} cho {learner.full_name}.",
        )
        return redirect("certificate-list")

    selection = request.POST if request.method == "POST" else request.GET
    class_id = selection.get("training_class") or initial.get("training_class")
    learner_id = selection.get("learner") or initial.get("learner")
    training_class = issue_form.fields["training_class"].queryset.filter(pk=class_id).first() if class_id else None
    learner = issue_form.fields["learner"].queryset.filter(pk=learner_id).first() if learner_id else None

    result = None
    active_certificate = None
    if training_class and learner:
        result = FinalCourseResult.objects.filter(
            training_class=training_class,
            learner=learner,
        ).first()
        active_certificate = Certificate.objects.filter(
            training_class=training_class,
            learner=learner,
            status=Certificate.Status.ISSUED,
        ).order_by("-issued_at", "-pk").first()

    has_passing_result = bool(
        result
        and not result.is_absent
        and result.score is not None
        and result.score >= training_class.course.pass_threshold
    )
    results_locked = bool(training_class and training_class.final_results_locked)
    checks = [
        {
            "label": "Kết quả đã chốt",
            "description": (
                "Bảng điểm đã được chốt; kết quả đủ cơ sở xét cấp."
                if results_locked
                else "Bảng điểm cuối khóa chưa được chốt."
            ),
            "passed": results_locked,
        },
        {
            "label": "Kết quả học tập đạt",
            "description": (
                f"Điểm {result.score} đạt ngưỡng {training_class.course.pass_threshold}."
                if has_passing_result
                else (
                    "Học viên được đánh dấu vắng, không đủ điều kiện cấp chứng nhận."
                    if result and result.is_absent
                    else (
                        f"Điểm {result.score} chưa đạt ngưỡng {training_class.course.pass_threshold}."
                        if result and result.score is not None
                        else "Chưa có điểm để đối chiếu điều kiện."
                    )
                )
            ),
            "passed": has_passing_result,
        },
        {
            "label": "Chưa có chứng nhận còn hiệu lực",
            "description": (
                f"{active_certificate.certificate_number} đang còn hiệu lực."
                if active_certificate
                else (
                    "Không có chứng nhận còn hiệu lực cho lớp học này."
                    if learner
                    else "Chọn học viên để kiểm tra chứng nhận hiện có."
                )
            ),
            "passed": bool(learner and not active_certificate),
        },
    ]
    passed_count = sum(check["passed"] for check in checks)
    can_issue = bool(training_class and learner and passed_count == len(checks))
    blocking_reason = next(
        (check["description"] for check in checks if not check["passed"]),
        "",
    )

    return render(
        request,
        "training/certificate_issue.html",
        {
            "active_menu": "certificates",
            "page_title": "Cấp chứng nhận",
            "page_description": "Chọn học viên, kiểm tra điều kiện và xác nhận cấp chứng nhận.",
            "breadcrumbs": [
                {"label": "Chứng nhận", "url": reverse("certificate-list")},
                {"label": "Cấp chứng nhận"},
            ],
            "issue_form": issue_form,
            "training_class": training_class,
            "learner": learner,
            "result": result,
            "checks": checks,
            "passed_count": passed_count,
            "can_issue": can_issue,
            "blocking_reason": blocking_reason,
            "certificate_number": _generate_certificate_number(),
        },
    )


@login_required
def certificate_detail(request, result_id):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    result = get_object_or_404(
        FinalCourseResult.objects.select_related("training_class__course", "learner"),
        pk=result_id,
        score__gte=F("training_class__course__pass_threshold"),
    )
    certificate = Certificate.objects.select_related("issued_by", "replaced_by").filter(
        training_class=result.training_class,
        learner=result.learner,
    ).order_by("-issued_at", "-pk").first()
    replaces_certificate = certificate.replaced_certificate.order_by("-replaced_at", "-pk").first() if certificate else None
    return render(
        request,
        "training/certificate_detail.html",
        {
            "active_menu": "certificates",
            "page_title": "Thông tin chi tiết chứng nhận",
            "breadcrumbs": [
                {"label": "Chứng nhận", "url": reverse("certificate-list")},
                {"label": "Thông tin chi tiết"},
            ],
            "result": result,
            "learner": result.learner,
            "training_class": result.training_class,
            "certificate": certificate,
            "replaces_certificate": replaces_certificate,
        },
    )


@login_required
def certificate_candidate_detail(request, class_id, learner_id):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    training_class = get_object_or_404(
        TrainingClass.objects.select_related("course"),
        pk=class_id,
    )
    learner = get_object_or_404(Learner, pk=learner_id)
    if not (
        Enrollment.objects.filter(training_class=training_class, learner=learner).exists()
        or FinalCourseResult.objects.filter(training_class=training_class, learner=learner).exists()
        or Certificate.objects.filter(training_class=training_class, learner=learner).exists()
    ):
        return HttpResponseNotFound("Học viên không thuộc lớp này.")

    result = FinalCourseResult.objects.filter(
        training_class=training_class,
        learner=learner,
    ).first()
    certificate = Certificate.objects.filter(
        training_class=training_class,
        learner=learner,
    ).order_by("-issued_at", "-pk").first()
    replaces_certificate = certificate.replaced_certificate.order_by("-replaced_at", "-pk").first() if certificate else None
    return render(
        request,
        "training/certificate_detail.html",
        {
            "active_menu": "certificates",
            "page_title": "Thông tin chi tiết chứng nhận",
            "breadcrumbs": [
                {"label": "Chứng nhận", "url": reverse("certificate-list")},
                {"label": "Thông tin chi tiết"},
            ],
            "result": result,
            "learner": learner,
            "training_class": training_class,
            "certificate": certificate,
            "replaces_certificate": replaces_certificate,
        },
    )


@login_required
def certificate_record_detail(request, pk):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    certificate = get_object_or_404(
        Certificate.objects.select_related("training_class__course", "learner", "issued_by", "replaced_by"),
        pk=pk,
    )
    result = FinalCourseResult.objects.filter(
        training_class=certificate.training_class,
        learner=certificate.learner,
    ).first()
    replaces_certificate = certificate.replaced_certificate.order_by("-replaced_at", "-pk").first()
    has_status_change = certificate.history.exclude(old_status=F("new_status")).exists()
    return render(
        request,
        "training/certificate_detail.html",
        {
            "active_menu": "certificates",
            "page_title": "Thông tin chi tiết chứng nhận",
            "breadcrumbs": [
                {"label": "Chứng nhận", "url": reverse("certificate-list")},
                {"label": "Thông tin chi tiết"},
            ],
            "result": result,
            "learner": certificate.learner,
            "training_class": certificate.training_class,
            "certificate": certificate,
            "replaces_certificate": replaces_certificate,
            "has_status_change": has_status_change,
        },
    )


@login_required
def certificate_pdf(request, pk):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    certificate = get_object_or_404(
        Certificate.objects.select_related("training_class__course", "learner", "issued_by"),
        pk=pk,
        status=Certificate.Status.ISSUED,
    )
    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from training.public_exam_views import _register_public_pdf_font

    font_name = _register_public_pdf_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CertificatePdfTitle",
        parent=styles["Title"],
        fontName="PublicExamSans-Bold",
        fontSize=22,
        leading=28,
        textColor=colors.HexColor("#183d57"),
        alignment=TA_CENTER,
        spaceAfter=12 * mm,
    )
    body_style = ParagraphStyle(
        "CertificatePdfBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=13,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#19344a"),
        spaceAfter=4 * mm,
    )
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=24 * mm,
        leftMargin=24 * mm,
        topMargin=30 * mm,
        bottomMargin=24 * mm,
        title=f"Chứng nhận {certificate.certificate_number}",
        author="Trung tâm Kỹ thuật Điện toán",
    )
    story = [
        Spacer(1, 16 * mm),
        Paragraph("TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN", body_style),
        Paragraph("CHỨNG NHẬN HOÀN THÀNH KHÓA HỌC", title_style),
        Paragraph("Trân trọng chứng nhận", body_style),
        Paragraph(escape(certificate.learner.full_name), title_style),
        Paragraph(
            f"đã hoàn thành khóa học <b>{escape(certificate.training_class.course.name)}</b>",
            body_style,
        ),
        Paragraph(f"Lớp: {escape(certificate.training_class.name)}", body_style),
        Paragraph(f"Số chứng nhận: {escape(certificate.certificate_number)}", body_style),
        Paragraph(f"Ngày cấp: {timezone.localtime(certificate.issued_at).strftime('%d/%m/%Y')}", body_style),
    ]
    document.build(story)

    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{certificate.certificate_number}.pdf"'
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def certificate_status_change(request, pk):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    certificate = get_object_or_404(Certificate.objects.select_related("training_class__course", "learner"), pk=pk)
    if certificate.status != Certificate.Status.ISSUED:
        messages.error(request, "Chỉ chứng nhận đã cấp mới được thay đổi trạng thái.")
        return redirect("certificate-list")

    form = CertificateStatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_status = form.cleaned_data["status"]
        reason = form.cleaned_data["reason"]
        old_status = certificate.status
        if new_status == old_status:
            messages.error(request, "Trạng thái mới phải khác trạng thái hiện tại.")
        else:
            if new_status == Certificate.Status.REPLACED:
                with transaction.atomic():
                    replacement = Certificate.objects.create(
                        training_class=certificate.training_class,
                        learner=certificate.learner,
                        certificate_number=_generate_certificate_number(),
                        status=Certificate.Status.ISSUED,
                        issued_by=request.user,
                        reason=reason,
                    )
                    CertificateHistory.objects.create(
                        certificate=replacement,
                        old_status=Certificate.Status.ISSUED,
                        new_status=Certificate.Status.ISSUED,
                        reason=reason,
                        changed_by=request.user,
                    )
                    certificate.status = Certificate.Status.REPLACED
                    certificate.reason = reason
                    certificate.replaced_by = replacement
                    certificate.replaced_at = timezone.now()
                    certificate.save(update_fields=["status", "reason", "replaced_by", "replaced_at"])
                    CertificateHistory.objects.create(
                        certificate=certificate,
                        old_status=old_status,
                        new_status=Certificate.Status.REPLACED,
                        reason=reason,
                        changed_by=request.user,
                    )
                    record_audit(
                        request.user,
                        AuditLog.Action.REPLACE,
                        certificate,
                        old_values={"status": old_status},
                        new_values={"status": Certificate.Status.REPLACED, "replaced_by_id": replacement.pk},
                        reason=reason,
                        object_label=f"{certificate.certificate_number} — {certificate.learner.full_name}",
                    )
                messages.success(request, f"Đã thay thế chứng nhận {certificate.certificate_number} bằng {replacement.certificate_number}.")
            else:
                certificate.status = new_status
                certificate.reason = reason
                certificate.save(update_fields=["status", "reason"])
                CertificateHistory.objects.create(
                    certificate=certificate,
                    old_status=old_status,
                    new_status=new_status,
                    reason=reason,
                    changed_by=request.user,
                )
                action = AuditLog.Action.REVOKE if new_status == Certificate.Status.REVOKED else AuditLog.Action.EDIT
                record_audit(
                    request.user,
                    action,
                    certificate,
                    old_values={"status": old_status},
                    new_values={"status": new_status},
                    reason=reason,
                    object_label=f"{certificate.certificate_number} — {certificate.learner.full_name}",
                )
                messages.success(request, f"Đã cập nhật trạng thái chứng nhận {certificate.certificate_number}.")
            return redirect("certificate-list")

    return render(
        request,
        "training/certificate_status_form.html",
        {
            "active_menu": "certificates",
            "page_title": "Cập nhật trạng thái chứng nhận",
            "breadcrumbs": [{"label": "Chứng nhận", "url": reverse("certificate-list")}, {"label": "Cập nhật trạng thái"}],
            "certificate": certificate,
            "form": form,
        },
    )


@login_required
def certificate_history(request, pk):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    certificate = get_object_or_404(
        Certificate.objects.select_related("training_class__course", "learner").prefetch_related(
            "replaced_certificate"
        ),
        pk=pk,
    )
    history = certificate.history.select_related("changed_by").all()
    replacement_sources = list(certificate.replaced_certificate.all())
    return render(
        request,
        "training/certificate_history.html",
        {
            "active_menu": "certificates",
            "page_title": "Lịch sử chứng nhận",
            "breadcrumbs": [{"label": "Chứng nhận", "url": reverse("certificate-list")}, {"label": certificate.certificate_number}],
            "certificate": certificate,
            "history": history,
            "is_replacement_certificate": bool(replacement_sources),
            "replacement_reason": replacement_sources[0].reason if replacement_sources else "",
        },
    )


def _generate_exam_certificate_number():
    year = timezone.now().year
    prefix = f"EXC-{year}-"
    from training.models import ExamCertificate

    latest = ExamCertificate.objects.filter(certificate_number__startswith=prefix).order_by("-certificate_number").first()
    if latest:
        suffix = latest.certificate_number.rsplit("-", 1)[-1]
        seq = int(suffix) + 1 if suffix.isdigit() else 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


def _exam_certificate_period_ids_with_candidates_meeting_two_conditions():
    from training.models import ExamApplication, ExamCertificate, ExamScore

    applications = ExamApplication.objects.filter(
        status=ExamApplication.Status.VALID
    ).values_list("pk", "exam_period_id")
    latest_scores = {}
    scores = ExamScore.objects.filter(
        assignment__application__status=ExamApplication.Status.VALID,
        assignment__exam_room__exam_period_id=F(
            "assignment__application__exam_period_id"
        ),
    ).order_by("-published_at", "-pk").values(
        "assignment__application_id",
        "assignment__exam_room__exam_period_id",
        "score",
        "is_absent",
        "is_published",
    )
    for score in scores:
        pair = (
            score["assignment__exam_room__exam_period_id"],
            score["assignment__application_id"],
        )
        latest_scores.setdefault(pair, score)

    active_pairs = set(
        ExamCertificate.objects.filter(
            status=ExamCertificate.Status.ISSUED
        ).values_list("exam_period_id", "application_id")
    )
    period_ids = set()
    for application_id, period_id in applications:
        pair = (period_id, application_id)
        score = latest_scores.get(pair)
        has_valid_score = bool(
            score and not score["is_absent"] and score["score"] is not None
        )
        has_passing_score = bool(
            has_valid_score and score["score"] >= EXAM_CERTIFICATE_MIN_SCORE
        )
        score_is_published = bool(score and score["is_published"])
        has_no_active_certificate = pair not in active_pairs
        passed_conditions = sum(
            (
                True,
                has_valid_score,
                has_passing_score,
                score_is_published,
                has_no_active_certificate,
            )
        )
        if passed_conditions >= 2:
            period_ids.add(period_id)
    return period_ids


class ExamCertificateIssueForm(forms.Form):
    exam_period = forms.ModelChoiceField(
        label="Đợt thi",
        queryset=None,
        empty_label="Chọn đợt thi",
    )
    application = forms.ModelChoiceField(
        label="Thí sinh",
        queryset=None,
        empty_label="Chọn thí sinh",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from training.models import ExamApplication, ExamPeriod

        eligible_period_ids = (
            _exam_certificate_period_ids_with_candidates_meeting_two_conditions()
        )
        self.fields["exam_period"].queryset = ExamPeriod.objects.filter(
            pk__in=eligible_period_ids
        ).order_by("-exam_date")
        applications = ExamApplication.objects.filter(
            status=ExamApplication.Status.VALID
        ).order_by("full_name")
        period_id = (
            self.data.get("exam_period")
            if self.is_bound
            else self.initial.get("exam_period")
        )
        if period_id and self.fields["exam_period"].queryset.filter(
            pk=period_id
        ).exists():
            applications = applications.filter(exam_period_id=period_id)
        else:
            applications = applications.none()
        self.fields["application"].queryset = applications
        for field in self.fields.values():
            field.widget.attrs["class"] = "cce-select"

    def clean(self):
        cleaned = super().clean()
        from training.models import ExamCertificate, ExamScore

        period = cleaned.get("exam_period")
        application = cleaned.get("application")
        if period and application:
            score = (
                ExamScore.objects.filter(
                    assignment__exam_room__exam_period=period,
                    assignment__application=application,
                )
                .select_related("assignment__exam_room__exam_period", "assignment__application")
                .order_by("-published_at", "-pk")
                .first()
            )
            if score is None:
                raise forms.ValidationError("Thí sinh chưa có điểm trong đợt thi này.")
            if score.is_absent or score.score is None:
                raise forms.ValidationError("Thí sinh chưa có điểm hợp lệ hoặc vắng thi.")
            if score.score < EXAM_CERTIFICATE_MIN_SCORE:
                raise forms.ValidationError(
                    f"Thí sinh chưa đạt ngưỡng tối thiểu {EXAM_CERTIFICATE_MIN_SCORE} để cấp chứng chỉ."
                )
            if not score.is_published:
                raise forms.ValidationError("Điểm phải được công bố trước khi cấp chứng chỉ.")
            if ExamCertificate.objects.filter(exam_period=period, application=application, status=ExamCertificate.Status.ISSUED).exists():
                raise forms.ValidationError("Thí sinh này đã có chứng chỉ còn hiệu lực trong đợt thi.")
        return cleaned


class ExamCertificateStatusForm(forms.Form):
    status = forms.ChoiceField(label="Trạng thái mới", choices=ExamCertificate.Status.choices)
    reason = forms.CharField(label="Lý do", widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}), required=False)

    def clean(self):
        cleaned = super().clean()
        reason = (cleaned.get("reason") or "").strip()
        if not reason:
            self.add_error("reason", "Vui lòng nhập lý do bắt buộc.")
        return cleaned


def _exam_certificate_issue_checks(period, application):
    from training.models import ExamApplication, ExamCertificate, ExamScore

    if not period or not application:
        checks = [
            {
                "label": "Hồ sơ thí sinh hợp lệ",
                "description": "Chọn đợt thi và thí sinh để kiểm tra hồ sơ.",
                "passed": False,
            },
            {
                "label": "Có điểm thi hợp lệ và không vắng thi",
                "description": "Chọn thí sinh để kiểm tra kết quả trong đợt thi.",
                "passed": False,
            },
            {
                "label": f"Đạt điểm tối thiểu {EXAM_CERTIFICATE_MIN_SCORE}",
                "description": f"Điểm thi phải đạt từ {EXAM_CERTIFICATE_MIN_SCORE} trở lên.",
                "passed": False,
            },
            {
                "label": "Điểm đã được công bố",
                "description": "Điểm phải được công bố trước khi cấp chứng chỉ.",
                "passed": False,
            },
            {
                "label": "Chưa có chứng chỉ còn hiệu lực",
                "description": "Mỗi thí sinh chỉ có một chứng chỉ còn hiệu lực trong đợt thi.",
                "passed": False,
            },
        ]
        return None, None, checks, False

    score = ExamScore.objects.filter(
        assignment__exam_room__exam_period=period,
        assignment__application=application,
    ).select_related(
        "assignment__exam_room__exam_period",
        "assignment__application",
    ).order_by("-published_at", "-pk").first()
    active_certificate = ExamCertificate.objects.filter(
        exam_period=period,
        application=application,
        status=ExamCertificate.Status.ISSUED,
    ).order_by("-issued_at", "-pk").first()
    has_valid_score = bool(
        score and not score.is_absent and score.score is not None
    )
    checks = [
        {
            "label": "Hồ sơ thí sinh hợp lệ",
            "description": (
                "Hồ sơ đã được xác nhận hợp lệ."
                if application.status == ExamApplication.Status.VALID
                else "Hồ sơ thí sinh chưa hợp lệ."
            ),
            "passed": application.status == ExamApplication.Status.VALID,
        },
        {
            "label": "Có điểm thi hợp lệ và không vắng thi",
            "description": (
                "Đã có điểm thi hợp lệ trong đợt thi này."
                if has_valid_score
                else (
                    "Thí sinh vắng thi."
                    if score and score.is_absent
                    else "Chưa có điểm thi hợp lệ trong đợt thi này."
                )
            ),
            "passed": has_valid_score,
        },
        {
            "label": f"Đạt điểm tối thiểu {EXAM_CERTIFICATE_MIN_SCORE}",
            "description": (
                f"Điểm {score.score} đạt ngưỡng tối thiểu {EXAM_CERTIFICATE_MIN_SCORE}."
                if has_valid_score and score.score >= EXAM_CERTIFICATE_MIN_SCORE
                else (
                    f"Điểm {score.score} chưa đạt ngưỡng tối thiểu {EXAM_CERTIFICATE_MIN_SCORE}."
                    if has_valid_score
                    else "Chưa có điểm hợp lệ để đối chiếu."
                )
            ),
            "passed": bool(
                has_valid_score and score.score >= EXAM_CERTIFICATE_MIN_SCORE
            ),
        },
        {
            "label": "Điểm đã được công bố",
            "description": (
                "Kết quả thi đã được công bố."
                if score.is_published
                else "Điểm thi chưa được công bố."
            ) if score else "Chưa có điểm để kiểm tra trạng thái công bố.",
            "passed": bool(score and score.is_published),
        },
        {
            "label": "Chưa có chứng chỉ còn hiệu lực",
            "description": (
                f"Chứng chỉ {active_certificate.certificate_number} đang còn hiệu lực."
                if active_certificate
                else "Thí sinh chưa có chứng chỉ còn hiệu lực trong đợt thi này."
            ),
            "passed": not active_certificate,
        },
    ]
    passed_count = sum(check["passed"] for check in checks)
    return score, active_certificate, checks, passed_count == len(checks)


@login_required
def exam_certificate_issue(request):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate, ExamCertificateHistory

    selection = request.POST if request.method == "POST" else request.GET
    initial = {
        "exam_period": selection.get("issue_period") or selection.get("exam_period") or "",
        "application": selection.get("issue_application") or selection.get("application") or "",
    }
    issue_form = ExamCertificateIssueForm(
        request.POST if request.method == "POST" else None,
        initial=initial if request.method == "GET" else None,
    )
    period_id = initial["exam_period"]
    application_id = initial["application"]
    period = (
        issue_form.fields["exam_period"].queryset.filter(pk=period_id).first()
        if period_id
        else None
    )
    application = (
        issue_form.fields["application"].queryset.filter(pk=application_id).first()
        if application_id
        else None
    )
    if request.method == "POST" and issue_form.is_valid():
        period = issue_form.cleaned_data["exam_period"]
        application = issue_form.cleaned_data["application"]
        issue_reason = "Đã đạt kỳ thi và điểm đã được công bố."
        with transaction.atomic():
            certificate = ExamCertificate.objects.create(
                exam_period=period,
                application=application,
                certificate_number=_generate_exam_certificate_number(),
                status=ExamCertificate.Status.ISSUED,
                issued_by=request.user,
                reason=issue_reason,
            )
            ExamCertificateHistory.objects.create(
                certificate=certificate,
                old_status=ExamCertificate.Status.ISSUED,
                new_status=ExamCertificate.Status.ISSUED,
                reason=issue_reason,
                changed_by=request.user,
            )
            record_audit(
                request.user,
                AuditLog.Action.ISSUE,
                certificate,
                old_values={},
                new_values={
                    "exam_period_id": period.pk,
                    "application_id": application.pk,
                    "certificate_number": certificate.certificate_number,
                    "status": certificate.status,
                },
                reason=issue_reason,
                object_label=f"{certificate.certificate_number} — {application.full_name}",
            )
        messages.success(
            request,
            f"Đã cấp chứng chỉ {certificate.certificate_number} cho {application.full_name}.",
        )
        return redirect("exam-certificate-list")

    score, active_certificate, checks, can_issue = _exam_certificate_issue_checks(
        period,
        application,
    )
    passed_count = sum(check["passed"] for check in checks)
    blocking_reason = next(
        (check["description"] for check in checks if not check["passed"]),
        "",
    )
    return render(
        request,
        "training/exam_certificate_issue.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Cấp chứng chỉ khảo thí",
            "page_description": "Chọn thí sinh, kiểm tra điều kiện khảo thí và xác nhận cấp chứng chỉ.",
            "breadcrumbs": [
                {"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")},
                {"label": "Cấp chứng chỉ"},
            ],
            "issue_form": issue_form,
            "period": period,
            "application": application,
            "score": score,
            "active_certificate": active_certificate,
            "checks": checks,
            "passed_count": passed_count,
            "can_issue": can_issue,
            "blocking_reason": blocking_reason,
            "certificate_number": _generate_exam_certificate_number(),
        },
    )


@login_required
def exam_certificate_list(request):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamApplication, ExamCertificate, ExamPeriod, ExamScore

    published_passing_scores = ExamScore.objects.filter(
        assignment__application_id=OuterRef("application_id"),
        assignment__exam_room__exam_period_id=OuterRef("exam_period_id"),
        score__isnull=False,
        score__gte=5,
        is_absent=False,
        is_published=True,
    ).order_by("-published_at", "-pk")
    qs = ExamCertificate.objects.select_related(
        "exam_period", "application", "issued_by"
    ).prefetch_related(
        "history__changed_by__groups",
        "replaced_certificate",
    ).annotate(
        certificate_score_id=Subquery(published_passing_scores.values("pk")[:1])
    )
    keyword = (request.GET.get("q") or request.GET.get("keyword") or "").strip()
    period_id = request.GET.get("period") or request.GET.get("period_id")
    application_id = request.GET.get("application_id")
    status = request.GET.get("status") or ""

    if keyword:
        qs = qs.filter(
            Q(application__full_name__icontains=keyword)
            | Q(application__id_number__icontains=keyword)
            | Q(exam_period__name__icontains=keyword)
            | Q(certificate_number__icontains=keyword)
        )
    if period_id:
        qs = qs.filter(exam_period_id=period_id)
    if application_id:
        qs = qs.filter(application_id=application_id)
    if status and status != "pending":
        qs = qs.filter(status=status)

    certificates = list(qs.order_by("-issued_at", "-pk")) if status != "pending" else []
    certificate_score_ids = {
        certificate.certificate_score_id
        for certificate in certificates
        if certificate.certificate_score_id is not None
    }
    certificate_scores = {
        score.pk: score
        for score in ExamScore.objects.filter(pk__in=certificate_score_ids)
    }
    scores = ExamScore.objects.filter(
        assignment__application__status=ExamApplication.Status.VALID,
        assignment__exam_room__exam_period_id=F("assignment__application__exam_period_id"),
        score__isnull=False,
        is_absent=False,
        is_published=True,
    ).select_related(
        "assignment__application__exam_period",
        "assignment__application",
        "assignment__exam_room__exam_period",
    ).order_by("-published_at", "-pk")
    if period_id:
        scores = scores.filter(assignment__exam_room__exam_period_id=period_id)
    if application_id:
        scores = scores.filter(assignment__application_id=application_id)
    if keyword:
        scores = scores.filter(
            Q(assignment__application__full_name__icontains=keyword)
            | Q(assignment__application__id_number__icontains=keyword)
            | Q(assignment__exam_room__exam_period__name__icontains=keyword)
        )

    eligible_scores = {}
    scores_by_pair = {}
    for score in scores:
        pair = (score.assignment.exam_room.exam_period_id, score.assignment.application_id)
        scores_by_pair.setdefault(pair, score)
        if score.score >= EXAM_CERTIFICATE_MIN_SCORE:
            eligible_scores.setdefault(pair, score)

    rows = [
        {
            "certificate": certificate,
            "application": certificate.application,
            "period": certificate.exam_period,
            "status": certificate.status,
            "is_pending": False,
            "score": certificate_scores.get(certificate.certificate_score_id),
            "sort_date": certificate.issued_at,
            "sort_id": certificate.pk,
        }
        for certificate in certificates
    ]

    active_pairs = set(
        ExamCertificate.objects.filter(status=ExamCertificate.Status.ISSUED).values_list(
            "exam_period_id", "application_id"
        )
    )
    if status in ("", "pending"):
        for (candidate_period_id, candidate_application_id), score in eligible_scores.items():
            if (candidate_period_id, candidate_application_id) in active_pairs:
                continue
            application = score.assignment.application
            period = score.assignment.exam_room.exam_period
            rows.append(
                {
                    "certificate": None,
                    "application": application,
                    "period": period,
                    "status": "pending",
                    "is_pending": True,
                    "score": score,
                    "sort_date": score.published_at or score.entered_at or application.registered_at,
                    "sort_id": application.pk,
                }
            )

    rows.sort(key=lambda row: (row["sort_date"], row["sort_id"]), reverse=True)
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))

    periods = ExamPeriod.objects.order_by("-exam_date")
    published_scores = ExamScore.objects.filter(
        is_published=True,
        is_absent=False,
        score__isnull=False,
        score__gte=EXAM_CERTIFICATE_MIN_SCORE,
    )
    total_eligible = published_scores.count()
    total_issued = ExamCertificate.objects.filter(status=ExamCertificate.Status.ISSUED).count()
    total_revoked = ExamCertificate.objects.filter(status=ExamCertificate.Status.REVOKED).count()
    if published_scores.exists():
        pass_rate = round(
            (
                published_scores.filter(
                    score__gte=EXAM_CERTIFICATE_MIN_SCORE
                ).count()
                / published_scores.count()
            )
            * 100,
            1,
        )
    else:
        pass_rate = 0

    return render(
        request,
        "training/exam_certificate_list.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Chứng chỉ khảo thí",
            "page_description": "Quản lý cấp, thu hồi và theo dõi chứng chỉ khảo thí theo từng đợt thi.",
            "breadcrumbs": [{"label": "Chứng chỉ khảo thí"}],
            "page_obj": page_obj,
            "periods": periods,
            "keyword": keyword,
            "search_query": keyword,
            "period_id": period_id,
            "selected_period": period_id or "",
            "application_id": application_id,
            "status": status,
            "selected_status": status,
            "total_eligible": total_eligible,
            "total_issued": total_issued,
            "total_revoked": total_revoked,
            "pass_rate": pass_rate,
        },
    )


@login_required
def exam_certificate_record_detail(request, pk):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate, ExamScore

    certificate = get_object_or_404(
        ExamCertificate.objects.select_related("exam_period", "application", "issued_by", "replaced_by").prefetch_related(
            "replaced_certificate"
        ),
        pk=pk,
    )
    score = ExamScore.objects.filter(
        assignment__exam_room__exam_period=certificate.exam_period,
        assignment__application=certificate.application,
    ).order_by("-published_at", "-pk").first()
    replaces_certificate = certificate.replaced_certificate.order_by("-replaced_at", "-pk").first()
    has_status_change = certificate.history.exclude(old_status=F("new_status")).exists()
    reason_to_display = ""
    if has_status_change:
        reason_to_display = certificate.reason
    elif replaces_certificate:
        reason_to_display = replaces_certificate.reason
    return render(
        request,
        "training/exam_certificate_detail.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Thông tin chi tiết chứng chỉ",
            "breadcrumbs": [
                {"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")},
                {"label": "Thông tin chi tiết"},
            ],
            "certificate": certificate,
            "application": certificate.application,
            "period": certificate.exam_period,
            "score": score,
            "replaces_certificate": replaces_certificate,
            "has_status_change": has_status_change,
            "reason_to_display": reason_to_display,
        },
    )


@login_required
def exam_certificate_candidate_detail(request, period_id, application_id):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamApplication, ExamPeriod

    period = get_object_or_404(ExamPeriod, pk=period_id)
    application = get_object_or_404(
        ExamApplication.objects.select_related("exam_period"),
        pk=application_id,
        exam_period=period,
    )
    score, _, eligibility_checks, can_issue = _exam_certificate_issue_checks(
        period,
        application,
    )
    return render(
        request,
        "training/exam_certificate_detail.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Thông tin thí sinh",
            "breadcrumbs": [
                {"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")},
                {"label": "Thông tin thí sinh"},
            ],
            "certificate": None,
            "application": application,
            "period": period,
            "score": score,
            "has_status_change": False,
            "eligibility_checks": eligibility_checks,
            "can_issue": can_issue,
        },
    )


def _build_exam_certificate_pdf(certificate):
    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph
    from training.models import ExamScore
    from training.public_exam_views import _register_public_pdf_font

    score = ExamScore.objects.filter(
        assignment__exam_room__exam_period=certificate.exam_period,
        assignment__application=certificate.application,
        is_published=True,
        is_absent=False,
        score__isnull=False,
    ).order_by("-published_at", "-pk").first()
    font_name = _register_public_pdf_font()
    navy = colors.HexColor("#173B57")
    ink = colors.HexColor("#19344A")
    muted = colors.HexColor("#587186")
    teal = colors.HexColor("#238F84")
    gold = colors.HexColor("#C29A59")
    border = colors.HexColor("#D7E0E7")
    pale = colors.HexColor("#F4F7F9")
    white = colors.white

    output = BytesIO()
    page = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    page.setTitle(f"Chứng chỉ {certificate.certificate_number}")
    page.setAuthor("Trung tâm Kỹ thuật Điện toán")
    page_width, page_height = A4
    center_x = page_width / 2
    content_width = page_width - 50 * mm
    content_left = (page_width - content_width) / 2

    page.setFillColor(white)
    page.rect(0, 0, page_width, page_height, stroke=0, fill=1)
    page.setStrokeColor(navy)
    page.setLineWidth(1.25)
    page.roundRect(
        12 * mm,
        12 * mm,
        page_width - 24 * mm,
        page_height - 24 * mm,
        4 * mm,
        stroke=1,
        fill=0,
    )
    page.setStrokeColor(gold)
    page.setLineWidth(0.65)
    page.roundRect(
        15 * mm,
        15 * mm,
        page_width - 30 * mm,
        page_height - 30 * mm,
        3 * mm,
        stroke=1,
        fill=0,
    )

    badge_width = 25 * mm
    badge_height = 15 * mm
    badge_x = center_x - badge_width / 2
    badge_y = page_height - 43 * mm
    page.setFillColor(navy)
    page.roundRect(
        badge_x,
        badge_y,
        badge_width,
        badge_height,
        3 * mm,
        stroke=0,
        fill=1,
    )
    page.setFillColor(white)
    page.setFont("PublicExamSans-Bold", 12)
    page.drawCentredString(center_x, badge_y + 4.8 * mm, "CCE")

    page.setFillColor(muted)
    page.setFont("PublicExamSans-Bold", 9.5)
    page.drawCentredString(
        center_x,
        page_height - 55 * mm,
        "TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN",
    )
    page.setFont(font_name, 8)
    page.drawCentredString(
        center_x,
        page_height - 61 * mm,
        "TRƯỜNG ĐẠI HỌC BÁCH KHOA - ĐHQG-HCM",
    )

    page.setFillColor(navy)
    page.setFont("PublicExamSans-Bold", 23)
    page.drawCentredString(
        center_x,
        page_height - 83 * mm,
        "CHỨNG CHỈ KHẢO THÍ",
    )
    divider_y = page_height - 91 * mm
    page.setStrokeColor(gold)
    page.setLineWidth(0.8)
    page.line(center_x - 25 * mm, divider_y, center_x - 2 * mm, divider_y)
    page.line(center_x + 2 * mm, divider_y, center_x + 25 * mm, divider_y)
    page.saveState()
    page.translate(center_x, divider_y)
    page.rotate(45)
    page.setFillColor(teal)
    page.rect(-1.3 * mm, -1.3 * mm, 2.6 * mm, 2.6 * mm, stroke=0, fill=1)
    page.restoreState()

    page.setFillColor(muted)
    page.setFont(font_name, 11)
    page.drawCentredString(
        center_x,
        page_height - 108 * mm,
        "Trân trọng chứng nhận",
    )

    candidate_name = str(certificate.application.full_name)
    name_style = ParagraphStyle(
        "ExamCertificatePdfRecipient",
        fontName="PublicExamSans-Bold",
        fontSize=26,
        leading=32,
        alignment=TA_CENTER,
        textColor=navy,
    )
    name_paragraph = Paragraph(escape(candidate_name), name_style)
    name_top = page_height - 122 * mm
    _, name_height = name_paragraph.wrap(content_width, 42 * mm)
    name_paragraph.drawOn(page, content_left, name_top - name_height)

    page.setStrokeColor(teal)
    page.setLineWidth(1)
    name_bottom = name_top - name_height
    line_y = name_bottom - 4 * mm
    page.line(center_x - 27 * mm, line_y, center_x + 27 * mm, line_y)

    statement_style = ParagraphStyle(
        "ExamCertificatePdfStatement",
        fontName=font_name,
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
        textColor=ink,
    )
    statement = Paragraph(
        "đã đạt yêu cầu trong đợt thi<br/>"
        f'<font name="PublicExamSans-Bold" color="#173B57">'
        f"{escape(certificate.exam_period.name)}</font>",
        statement_style,
    )
    statement_width = content_width
    _, statement_height = statement.wrap(statement_width, 45 * mm)
    statement_top = line_y - 13 * mm
    statement.drawOn(
        page,
        center_x - statement_width / 2,
        statement_top - statement_height,
    )

    number_panel_width = 112 * mm
    number_panel_height = 22 * mm
    number_panel_x = center_x - number_panel_width / 2
    number_panel_y = statement_top - statement_height - 10 * mm - number_panel_height
    page.setFillColor(pale)
    page.setStrokeColor(border)
    page.setLineWidth(0.7)
    page.roundRect(
        number_panel_x,
        number_panel_y,
        number_panel_width,
        number_panel_height,
        3 * mm,
        stroke=1,
        fill=1,
    )
    page.setFillColor(muted)
    page.setFont("PublicExamSans-Bold", 7.5)
    page.drawCentredString(
        center_x,
        number_panel_y + 14 * mm,
        "SỐ CHỨNG CHỈ",
    )
    page.setFillColor(navy)
    page.setFont("PublicExamSans-Bold", 11)
    page.drawCentredString(
        center_x,
        number_panel_y + 6 * mm,
        str(certificate.certificate_number),
    )

    metrics = []
    if score and score.score is not None:
        metrics.append(("ĐIỂM THI", str(score.score)))
    metrics.append(
        ("NGÀY CẤP", timezone.localtime(certificate.issued_at).strftime("%d/%m/%Y"))
    )
    metric_y = number_panel_y - 31 * mm
    metric_height = 22 * mm
    metric_gap = 6 * mm
    metric_width = min(
        78 * mm,
        (content_width - metric_gap) / len(metrics),
    )
    metric_start_x = center_x - (
        len(metrics) * metric_width + (len(metrics) - 1) * metric_gap
    ) / 2
    for index, (label, value) in enumerate(metrics):
        x = metric_start_x + index * (metric_width + metric_gap)
        page.setFillColor(white)
        page.setStrokeColor(border)
        page.roundRect(
            x,
            metric_y,
            metric_width,
            metric_height,
            3 * mm,
            stroke=1,
            fill=1,
        )
        page.setFillColor(teal)
        page.setFont("PublicExamSans-Bold", 7)
        page.drawString(x + 5 * mm, metric_y + 14 * mm, label)
        page.setFillColor(ink)
        page.setFont("PublicExamSans-Bold", 11)
        page.drawString(x + 5 * mm, metric_y + 6 * mm, value)

    page.setFillColor(muted)
    page.setFont(font_name, 7.5)
    page.drawCentredString(
        center_x,
        22 * mm,
        "TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN | ĐHQG-HCM",
    )
    page.showPage()
    page.save()
    return output.getvalue()


def _exam_certificate_pdf_response(certificate):
    response = HttpResponse(
        _build_exam_certificate_pdf(certificate),
        content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="chung-chi-{certificate.certificate_number}.pdf"'
    )
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def exam_certificate_pdf(request, pk):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate

    certificate = get_object_or_404(
        ExamCertificate.objects.select_related("exam_period", "application", "issued_by"),
        pk=pk,
        status=ExamCertificate.Status.ISSUED,
    )
    return _exam_certificate_pdf_response(certificate)


@login_required
def exam_certificate_status_change(request, pk):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate, ExamCertificateHistory

    certificate = get_object_or_404(ExamCertificate.objects.select_related("exam_period", "application"), pk=pk)
    if certificate.status != ExamCertificate.Status.ISSUED:
        messages.error(request, "Chỉ chứng chỉ đã cấp mới được thay đổi trạng thái.")
        return redirect("exam-certificate-list")

    form = ExamCertificateStatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_status = form.cleaned_data["status"]
        reason = form.cleaned_data["reason"]
        old_status = certificate.status
        if new_status == old_status:
            messages.error(request, "Trạng thái mới phải khác trạng thái hiện tại.")
        elif (
            new_status == ExamCertificate.Status.ISSUED
            and ExamCertificate.objects.filter(
                exam_period=certificate.exam_period,
                application=certificate.application,
                status=ExamCertificate.Status.ISSUED,
            ).exclude(pk=certificate.pk).exists()
        ):
            messages.error(
                request,
                "Thí sinh đã có chứng chỉ còn hiệu lực trong đợt thi này.",
            )
        else:
            if new_status == ExamCertificate.Status.REPLACED:
                with transaction.atomic():
                    replacement = ExamCertificate.objects.create(
                        exam_period=certificate.exam_period,
                        application=certificate.application,
                        certificate_number=_generate_exam_certificate_number(),
                        status=ExamCertificate.Status.ISSUED,
                        issued_by=request.user,
                        reason=reason,
                    )
                    ExamCertificateHistory.objects.create(
                        certificate=replacement,
                        old_status=ExamCertificate.Status.ISSUED,
                        new_status=ExamCertificate.Status.ISSUED,
                        reason=reason,
                        changed_by=request.user,
                    )
                    certificate.status = ExamCertificate.Status.REPLACED
                    certificate.reason = reason
                    certificate.replaced_by = replacement
                    certificate.replaced_at = timezone.now()
                    certificate.save(update_fields=["status", "reason", "replaced_by", "replaced_at"])
                    ExamCertificateHistory.objects.create(
                        certificate=certificate,
                        old_status=old_status,
                        new_status=ExamCertificate.Status.REPLACED,
                        reason=reason,
                        changed_by=request.user,
                    )
                    record_audit(
                        request.user,
                        AuditLog.Action.REPLACE,
                        certificate,
                        old_values={"status": old_status},
                        new_values={"status": ExamCertificate.Status.REPLACED, "replaced_by_id": replacement.pk},
                        reason=reason,
                        object_label=f"{certificate.certificate_number} — {certificate.application.full_name}",
                    )
                messages.success(request, f"Đã thay thế chứng chỉ {certificate.certificate_number} bằng {replacement.certificate_number}.")
            else:
                certificate.status = new_status
                certificate.reason = reason
                certificate.save(update_fields=["status", "reason"])
                ExamCertificateHistory.objects.create(
                    certificate=certificate,
                    old_status=old_status,
                    new_status=new_status,
                    reason=reason,
                    changed_by=request.user,
                )
                action = AuditLog.Action.REVOKE if new_status == ExamCertificate.Status.REVOKED else AuditLog.Action.EDIT
                record_audit(
                    request.user,
                    action,
                    certificate,
                    old_values={"status": old_status},
                    new_values={"status": new_status},
                    reason=reason,
                    object_label=f"{certificate.certificate_number} — {certificate.application.full_name}",
                )
                messages.success(request, f"Đã cập nhật trạng thái chứng chỉ {certificate.certificate_number}.")
            return redirect("exam-certificate-list")

    return render(
        request,
        "training/exam_certificate_status_form.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Cập nhật trạng thái chứng chỉ",
            "breadcrumbs": [{"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")}, {"label": "Cập nhật trạng thái"}],
            "certificate": certificate,
            "form": form,
        },
    )


@login_required
def exam_certificate_history(request, pk):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate

    certificate = get_object_or_404(
        ExamCertificate.objects.select_related("exam_period", "application").prefetch_related(
            "replaced_certificate"
        ),
        pk=pk,
    )
    history = certificate.history.select_related("changed_by").all()
    replacement_sources = list(certificate.replaced_certificate.all())
    return render(
        request,
        "training/exam_certificate_history.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Lịch sử chứng chỉ",
            "breadcrumbs": [{"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")}, {"label": certificate.certificate_number}],
            "certificate": certificate,
            "history": history,
            "is_replacement_certificate": bool(replacement_sources),
            "replacement_reason": replacement_sources[0].reason if replacement_sources else "",
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
            "transfer_url": f"{reverse('class-transfer')}?source_class={training_class.pk}",
            "transfer_history_url": (
                f"{reverse('class-transfer-history')}?training_class={training_class.pk}"
            ),
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
                learner = form.cleaned_data["learner"]
                enrollment, created = Enrollment.objects.get_or_create(
                    learner=learner,
                    training_class=training_class,
                )
                if created:
                    record_audit(
                        request.user,
                        AuditLog.Action.CREATE,
                        enrollment,
                        new_values=snapshot_instance(enrollment),
                    )
                    messages.success(request, "Học viên đã được ghi danh vào lớp.")
                    return redirect("class-detail", class_id=class_id)
                form.add_error("learner", "Học viên này đã được ghi danh vào lớp.")
    else:
        form = EnrollmentForm(training_class=training_class)

    class_closed = training_class.status == TrainingClass.Status.CLOSED
    class_full = training_class.enrolled_count >= training_class.max_students
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
            "class_closed": class_closed,
            "class_full": class_full,
            "is_post": request.method == "POST",
            "show_enrollment_form": request.method == "POST" or not class_closed and not class_full,
            "class_detail_url": reverse("class-detail", args=(class_id,)),
        },
    )


@education_required
@require_http_methods(["GET", "POST"])
def class_transfer(request):
    initial = {}
    if request.method == "GET" and request.GET.get("source_class"):
        initial["source_class"] = request.GET["source_class"]
    form = EnrollmentTransferForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        learner = form.cleaned_data["learner"]
        source_class = form.cleaned_data["source_class"]
        destination_class = form.cleaned_data["destination_class"]
        transfer_succeeded = False

        with transaction.atomic():
            locked_classes = {
                item.pk: item
                for item in TrainingClass.objects.select_for_update()
                .filter(pk__in=(source_class.pk, destination_class.pk))
                .order_by("pk")
            }
            source_class = locked_classes.get(source_class.pk)
            destination_class = locked_classes.get(destination_class.pk)

            if source_class is None:
                form.add_error("source_class", "Không tìm thấy lớp hiện tại.")
            elif destination_class is None:
                form.add_error("destination_class", "Không tìm thấy lớp mới.")
            else:
                enrollment = Enrollment.objects.select_for_update().filter(
                    learner=learner,
                    training_class=source_class,
                ).first()
                if enrollment is None:
                    form.add_error(
                        "source_class",
                        "Học viên không được ghi danh trong lớp hiện tại.",
                    )
                elif Enrollment.objects.filter(
                    learner=learner,
                    training_class=destination_class,
                ).exists():
                    form.add_error(
                        "destination_class",
                        "Học viên này đã có trong lớp mới.",
                    )
                elif destination_class.status != TrainingClass.Status.OPEN:
                    form.add_error(
                        "destination_class",
                        "Lớp mới đã đóng, không thể nhận học viên.",
                    )
                elif destination_class.enrolled_count >= destination_class.max_students:
                    form.add_error(
                        "destination_class",
                        "Lớp mới đã đạt sĩ số tối đa.",
                    )
                else:
                    old_class_name = source_class.name
                    new_class_name = destination_class.name
                    enrollment.delete()
                    Enrollment.objects.create(
                        learner=learner,
                        training_class=destination_class,
                    )
                    transfer = EnrollmentTransferHistory.objects.create(
                        learner=learner,
                        source_class=source_class,
                        destination_class=destination_class,
                        transferred_by=request.user,
                    )
                    record_audit(
                        request.user,
                        AuditLog.Action.EDIT,
                        transfer,
                        old_values={
                            "source_class": old_class_name,
                            "destination_class": old_class_name,
                        },
                        new_values={
                            "source_class": old_class_name,
                            "destination_class": new_class_name,
                        },
                        reason="Chuyển lớp.",
                    )
                    transfer_succeeded = True

        if transfer_succeeded:
            messages.success(
                request,
                f"Đã chuyển học viên {learner.full_name} từ lớp "
                f"{old_class_name} sang lớp {new_class_name}.",
            )
            return redirect("class-detail", class_id=destination_class.pk)

    source_class = form.cleaned_data.get("source_class") if form.is_bound else None
    destination_class = (
        form.cleaned_data.get("destination_class") if form.is_bound else None
    )
    destination_errors = [
        str(error) for error in form.errors.get("destination_class", [])
    ]
    show_full_capacity_popup = (
        "Lớp mới đã đạt sĩ số tối đa." in destination_errors
    )
    destination_classes = form.fields["destination_class"].queryset
    full_destination_class_ids = [
        str(item.pk)
        for item in destination_classes
        if item._enrollment_count >= item.max_students
    ]
    return render(
        request,
        "training/class_transfer_form.html",
        {
            "active_menu": "classes",
            "page_title": "Chuyển lớp",
            "page_description": "Chuyển học viên từ lớp hiện tại sang một lớp đang mở còn chỗ.",
            "breadcrumbs": [
                {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")},
                {"label": "Chuyển lớp"},
            ],
            "form": form,
            "has_enrollments": Enrollment.objects.exists(),
            "full_destination_class_ids": full_destination_class_ids,
            "show_full_capacity_popup": show_full_capacity_popup,
            "capacity_class_label": (
                form.fields["destination_class"].label_from_instance(destination_class)
                if destination_class and show_full_capacity_popup
                else ""
            ),
            "cancel_url": (
                reverse("class-detail", args=(source_class.pk,))
                if source_class
                else reverse("admin:training_trainingclass_changelist")
            ),
            "history_url": reverse("class-transfer-history"),
        },
    )


@education_required
def class_transfer_history(request):
    filter_form = EnrollmentTransferHistoryFilterForm(request.GET or None)
    history = EnrollmentTransferHistory.objects.select_related(
        "learner",
        "source_class",
        "destination_class",
        "transferred_by",
    )
    if filter_form.is_valid():
        learner = filter_form.cleaned_data.get("learner")
        training_class = filter_form.cleaned_data.get("training_class")
        if learner:
            history = history.filter(learner=learner)
        if training_class:
            history = history.filter(
                Q(source_class=training_class) | Q(destination_class=training_class)
            )

    page_obj = Paginator(history, PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [
        {
            "learner": item.learner.full_name,
            "source_class": item.source_class.name,
            "destination_class": item.destination_class.name,
            "transferred_at": timezone.localtime(item.transferred_at).strftime(
                "%d/%m/%Y %H:%M"
            ),
            "transferred_by": (
                item.transferred_by.get_full_name()
                or item.transferred_by.get_username()
                if item.transferred_by
                else "Tài khoản đã bị xóa"
            ),
        }
        for item in page_obj
    ]
    return render(
        request,
        "training/class_transfer_history.html",
        {
            "active_menu": "classes",
            "page_title": "Lịch sử chuyển lớp",
            "page_description": "Theo dõi học viên, lớp chuyển đi, lớp tiếp nhận, thời điểm và người thực hiện.",
            "breadcrumbs": [
                {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")},
                {"label": "Lịch sử chuyển lớp"},
            ],
            "filter_form": filter_form,
            "page_obj": page_obj,
            "rows": rows,
            "has_active_filters": bool(
                filter_form.is_valid()
                and (
                    filter_form.cleaned_data.get("learner")
                    or filter_form.cleaned_data.get("training_class")
                )
            ),
            "columns": [
                {"key": "learner", "label": "Học viên"},
                {"key": "source_class", "label": "Lớp cũ"},
                {"key": "destination_class", "label": "Lớp mới"},
                {"key": "transferred_at", "label": "Thời điểm"},
                {"key": "transferred_by", "label": "Người thực hiện"},
            ],
            "transfer_url": reverse("class-transfer"),
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
                    "locked": (
                        group.name == "Quản trị viên"
                        and feature["key"] in REQUIRED_ADMIN_FEATURES
                    ),
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
                feature["key"]: (
                    group.name == "Quản trị viên"
                    and feature["key"] in REQUIRED_ADMIN_FEATURES
                ) or f"{group.pk}:{feature['key']}" in selected
                for feature in features
            }
            for group in groups
        }
        with transaction.atomic():
            saved_denials = {
                (group_id, feature): deny_overrides
                for group_id, feature, deny_overrides in RoleFeaturePermission.objects.filter(
                    group__in=groups
                ).values_list("group_id", "feature", "deny_overrides")
            }
            for group in groups:
                for feature in features:
                    was_allowed = states[group.name][feature["key"]]
                    is_allowed = new_states[group.name][feature["key"]]
                    was_denied = saved_denials.get(
                        (group.pk, feature["key"]),
                        False,
                    )
                    deny_overrides = (
                        False
                        if is_allowed
                        else was_allowed or was_denied
                    )
                    RoleFeaturePermission.objects.update_or_create(
                        group=group,
                        feature=feature["key"],
                        defaults={
                            "allowed": is_allowed,
                            "deny_overrides": deny_overrides,
                        },
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
                        "locked": (
                            group.name == "Quản trị viên"
                            and feature["key"] in REQUIRED_ADMIN_FEATURES
                        ),
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
            "has_active_filters": bool(
                form.is_valid()
                and any(
                    form.cleaned_data.get(field)
                    for field in ("action", "actor", "from_date", "to_date")
                )
            ),
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
    exam_score_upload_sample,
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
