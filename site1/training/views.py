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
from django.db.models import Count, F, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from training.access import all_features, role_feature_states, user_has_feature
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
    Enrollment,
    EnrollmentTransferHistory,
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
    if request.user.is_authenticated:
        if user_has_feature(request.user, "dashboard"):
            return redirect("dashboard")
        if user_has_feature(request.user, "my-classes"):
            return redirect("my-class-list")
        if request.user.is_superuser:
            return redirect("admin:index")
        return redirect("profile")
    registration_periods = (
        ExamPeriod.objects.filter(
            status=ExamPeriod.Status.PUBLISHED,
            registration_deadline__gte=timezone.localdate(),
        )
        .annotate(candidate_count=Count("applications"))
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
        self.fields["learner"].queryset = Learner.objects.order_by("full_name")

    def clean(self):
        cleaned = super().clean()
        training_class = cleaned.get("training_class")
        learner = cleaned.get("learner")
        if training_class and learner:
            result = FinalCourseResult.objects.filter(training_class=training_class, learner=learner).first()
            if result is None:
                raise forms.ValidationError("Học viên chưa có kết quả cuối khóa trong lớp này.")
            if result.score < training_class.course.pass_threshold:
                raise forms.ValidationError("Học viên chưa đạt điều kiện cấp chứng nhận.")
            if Certificate.objects.filter(training_class=training_class, learner=learner, status=Certificate.Status.ISSUED).exists():
                raise forms.ValidationError("Học viên này đã có chứng nhận còn hiệu lực trong lớp.")
        return cleaned


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

    issue_form = CertificateIssueForm(request.POST if request.method == "POST" and "issue_certificate" in request.POST else None)
    if request.method == "POST" and "issue_certificate" in request.POST:
        if issue_form.is_valid():
            training_class = issue_form.cleaned_data["training_class"]
            learner = issue_form.cleaned_data["learner"]
            certificate = Certificate.objects.create(
                training_class=training_class,
                learner=learner,
                certificate_number=_generate_certificate_number(),
                status=Certificate.Status.ISSUED,
                issued_by=request.user,
                reason="Cấp chứng nhận theo kết quả cuối khóa.",
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
                reason="Cấp chứng nhận theo kết quả cuối khóa.",
                object_label=f"{certificate.certificate_number} — {learner.full_name}",
            )
            CertificateHistory.objects.create(
                certificate=certificate,
                old_status=Certificate.Status.ISSUED,
                new_status=Certificate.Status.ISSUED,
                reason="Cấp chứng nhận theo kết quả cuối khóa.",
                changed_by=request.user,
            )
            messages.success(request, f"Đã cấp chứng nhận {certificate.certificate_number} cho {learner.full_name}.")
            return redirect("certificate-list")
        messages.error(request, "Không thể cấp chứng nhận. Vui lòng kiểm tra lại thông tin và điều kiện.")

    qs = Certificate.objects.select_related("training_class__course", "learner", "issued_by")
    keyword = (request.GET.get("keyword") or "").strip()
    class_id = request.GET.get("class_id")
    learner_id = request.GET.get("learner_id")
    status = request.GET.get("status")

    if keyword:
        qs = qs.filter(
            Q(
                learner__full_name__icontains=keyword
            )
            | Q(training_class__name__icontains=keyword)
            | Q(certificate_number__icontains=keyword)
        )
    if class_id:
        qs = qs.filter(training_class_id=class_id)
    if learner_id:
        qs = qs.filter(learner_id=learner_id)
    if status:
        qs = qs.filter(status=status)

    page_obj = Paginator(qs.order_by("-issued_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))

    classes = TrainingClass.objects.select_related("course").order_by("name")
    learners = Learner.objects.order_by("full_name")

    return render(
        request,
        "training/certificate_list.html",
        {
            "active_menu": "certificates",
            "page_title": "Chứng nhận",
            "page_description": "Quản lý cấp, thu hồi và thay thế chứng nhận cho học viên đủ điều kiện.",
            "breadcrumbs": [{"label": "Chứng nhận"}],
            "page_obj": page_obj,
            "issue_form": issue_form,
            "status_choices": Certificate.Status.choices,
            "classes": classes,
            "learners": learners,
            "keyword": keyword,
            "class_id": class_id,
            "learner_id": learner_id,
            "status": status,
            "total_eligible": FinalCourseResult.objects.filter(score__gte=F("training_class__course__pass_threshold")).count(),
            "total_issued": Certificate.objects.filter(status=Certificate.Status.ISSUED).count(),
            "pending_paper": 0,
            "graduation_rate": "0%",
        },
    )


@login_required
def certificate_status_change(request, pk):
    if not user_has_feature(request.user, "certificates"):
        raise PermissionDenied

    certificate = get_object_or_404(Certificate.objects.select_related("training_class__course", "learner"), pk=pk)
    form = CertificateStatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_status = form.cleaned_data["status"]
        reason = form.cleaned_data["reason"]
        old_status = certificate.status
        if new_status == old_status:
            messages.error(request, "Trạng thái mới phải khác trạng thái hiện tại.")
        else:
            if new_status == Certificate.Status.REPLACED:
                replacement = Certificate.objects.create(
                    training_class=certificate.training_class,
                    learner=certificate.learner,
                    certificate_number=_generate_certificate_number(),
                    status=Certificate.Status.ISSUED,
                    issued_by=request.user,
                    reason=f"Thay thế chứng nhận gốc {certificate.certificate_number}.",
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

    certificate = get_object_or_404(Certificate.objects.select_related("training_class__course", "learner"), pk=pk)
    history = certificate.history.select_related("changed_by").all()
    return render(
        request,
        "training/certificate_history.html",
        {
            "active_menu": "certificates",
            "page_title": "Lịch sử chứng nhận",
            "breadcrumbs": [{"label": "Chứng nhận", "url": reverse("certificate-list")}, {"label": certificate.certificate_number}],
            "certificate": certificate,
            "history": history,
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

        self.fields["exam_period"].queryset = ExamPeriod.objects.order_by("-exam_date")
        self.fields["application"].queryset = ExamApplication.objects.filter(status=ExamApplication.Status.VALID).order_by("full_name")

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
                .first()
            )
            if score is None:
                raise forms.ValidationError("Thí sinh chưa có điểm trong đợt thi này.")
            if score.is_absent or score.score is None:
                raise forms.ValidationError("Thí sinh chưa có điểm hợp lệ hoặc vắng thi.")
            if score.score < 5:
                raise forms.ValidationError("Thí sinh chưa đạt ngưỡng tối thiểu để cấp chứng chỉ.")
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


@login_required
def exam_certificate_list(request):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamApplication, ExamCertificate, ExamCertificateHistory, ExamPeriod, ExamScore

    issue_form = ExamCertificateIssueForm(
        request.POST if request.method == "POST" and "issue_exam_certificate" in request.POST else None,
    )
    if request.method == "POST" and "issue_exam_certificate" in request.POST:
        if issue_form.is_valid():
            period = issue_form.cleaned_data["exam_period"]
            application = issue_form.cleaned_data["application"]
            certificate = ExamCertificate.objects.create(
                exam_period=period,
                application=application,
                certificate_number=_generate_exam_certificate_number(),
                status=ExamCertificate.Status.ISSUED,
                issued_by=request.user,
                reason="Cấp chứng chỉ theo điểm thi đã công bố.",
            )
            ExamCertificateHistory.objects.create(
                certificate=certificate,
                old_status=ExamCertificate.Status.ISSUED,
                new_status=ExamCertificate.Status.ISSUED,
                reason="Cấp chứng chỉ theo điểm thi đã công bố.",
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
                reason="Cấp chứng chỉ theo điểm thi đã công bố.",
                object_label=f"{certificate.certificate_number} — {application.full_name}",
            )
            messages.success(request, f"Đã cấp chứng chỉ {certificate.certificate_number} cho {application.full_name}.")
            return redirect("exam-certificate-list")
        messages.error(request, "Không thể cấp chứng chỉ. Vui lòng kiểm tra thông tin và điều kiện.")

    qs = ExamCertificate.objects.select_related("exam_period", "application", "issued_by")
    keyword = (request.GET.get("keyword") or "").strip()
    period_id = request.GET.get("period_id")
    application_id = request.GET.get("application_id")
    status = request.GET.get("status")

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
    if status:
        qs = qs.filter(status=status)

    page_obj = Paginator(qs.order_by("-issued_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))

    periods = ExamPeriod.objects.order_by("-exam_date")
    applications = ExamApplication.objects.filter(status=ExamApplication.Status.VALID).order_by("full_name")
    published_scores = ExamScore.objects.filter(is_published=True, is_absent=False, score__isnull=False, score__gte=5)
    total_eligible = published_scores.count()
    total_issued = ExamCertificate.objects.filter(status=ExamCertificate.Status.ISSUED).count()
    total_revoked = ExamCertificate.objects.filter(status=ExamCertificate.Status.REVOKED).count()
    if published_scores.exists():
        pass_rate = round((published_scores.filter(score__gte=Decimal("5.0")).count() / published_scores.count()) * 100, 1)
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
            "issue_form": issue_form,
            "status_choices": ExamCertificate.Status.choices,
            "periods": periods,
            "applications": applications,
            "keyword": keyword,
            "period_id": period_id,
            "application_id": application_id,
            "status": status,
            "total_eligible": total_eligible,
            "total_issued": total_issued,
            "total_revoked": total_revoked,
            "pass_rate": pass_rate,
        },
    )


@login_required
def exam_certificate_status_change(request, pk):
    if not user_has_feature(request.user, "exam-certificates"):
        raise PermissionDenied

    from training.models import ExamCertificate

    certificate = get_object_or_404(ExamCertificate.objects.select_related("exam_period", "application"), pk=pk)
    form = ExamCertificateStatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        new_status = form.cleaned_data["status"]
        reason = form.cleaned_data["reason"]
        old_status = certificate.status
        if new_status == old_status:
            messages.error(request, "Trạng thái mới phải khác trạng thái hiện tại.")
        else:
            if new_status == ExamCertificate.Status.REPLACED:
                replacement = ExamCertificate.objects.create(
                    exam_period=certificate.exam_period,
                    application=certificate.application,
                    certificate_number=_generate_exam_certificate_number(),
                    status=ExamCertificate.Status.ISSUED,
                    issued_by=request.user,
                    reason=f"Thay thế chứng chỉ gốc {certificate.certificate_number}.",
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

    certificate = get_object_or_404(ExamCertificate.objects.select_related("exam_period", "application"), pk=pk)
    history = certificate.history.select_related("changed_by").all()
    return render(
        request,
        "training/exam_certificate_history.html",
        {
            "active_menu": "exam-certificates",
            "page_title": "Lịch sử chứng chỉ",
            "breadcrumbs": [{"label": "Chứng chỉ khảo thí", "url": reverse("exam-certificate-list")}, {"label": certificate.certificate_number}],
            "certificate": certificate,
            "history": history,
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
            "results_url": reverse("class-results", args=(item.pk,)),
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
