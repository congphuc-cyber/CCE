
# =============================================================================
# EXAM VIEWS — Pha 1: Đợt thi + Hồ sơ đăng ký
# =============================================================================
import csv
import io

from django import forms as django_forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from training.access import user_has_feature
from training.audit import record_audit, snapshot_instance
from training.models import AuditLog, ExamApplication, ExamPeriod
from training.pagination import PAGE_SIZE


# ── Forms ────────────────────────────────────────────────────────────────────

class ExamPeriodForm(django_forms.ModelForm):
    class Meta:
        model = ExamPeriod
        fields = [
            "name", "cert_type", "exam_date",
            "registration_deadline", "max_candidates", "score_scale",
        ]
        widgets = {
            "exam_date": django_forms.DateInput(
                attrs={"type": "date", "class": "cce-input"}
            ),
            "registration_deadline": django_forms.DateInput(
                attrs={"type": "date", "class": "cce-input"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "cce-input"
            if name == "cert_type":
                field.widget.attrs["class"] = "cce-select"


class ExamApplicationFilterForm(django_forms.Form):
    q = django_forms.CharField(
        label="Tìm kiếm", required=False,
        widget=django_forms.TextInput(attrs={
            "class": "cce-input", "placeholder": "Họ tên, số CCCD...",
        }),
    )
    exam_period = django_forms.ModelChoiceField(
        label="Đợt thi", required=False, queryset=None,
        empty_label="Tất cả đợt thi",
        widget=django_forms.Select(attrs={"class": "cce-select"}),
    )
    source = django_forms.ChoiceField(
        label="Kênh đăng ký", required=False,
        choices=[
            ("", "Tất cả kênh"),
            ("online", "Trực tuyến"),
            ("google_form", "Google Form"),
            ("partner", "Đối tác"),
            ("direct", "Tại cơ sở"),
        ],
        widget=django_forms.Select(attrs={"class": "cce-select"}),
    )
    status = django_forms.ChoiceField(
        label="Trạng thái hồ sơ", required=False,
        choices=[
            ("", "Tất cả trạng thái"),
            ("valid", "Hợp lệ"),
            ("duplicate", "Trùng"),
        ],
        widget=django_forms.Select(attrs={"class": "cce-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exam_period"].queryset = ExamPeriod.objects.exclude(
            status=ExamPeriod.Status.DRAFT
        ).order_by("-exam_date")


# ── Đợt thi ──────────────────────────────────────────────────────────────────

@login_required
def exam_period_list(request):
    if not user_has_feature(request.user, "exam-periods"):
        raise PermissionDenied

    qs = ExamPeriod.objects.select_related("cert_type").order_by("-exam_date", "name")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    if q:
        qs = qs.filter(name__icontains=q)
    if status_filter:
        qs = qs.filter(status=status_filter)

    total_periods = ExamPeriod.objects.count()
    draft_count = ExamPeriod.objects.filter(status=ExamPeriod.Status.DRAFT).count()
    published_count = ExamPeriod.objects.filter(status=ExamPeriod.Status.PUBLISHED).count()
    closed_count = ExamPeriod.objects.filter(status=ExamPeriod.Status.CLOSED).count()

    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))

    for p in page_obj:
        if p.status == ExamPeriod.Status.DRAFT:
            p.app_pct = 0.0
            p.is_full = False
        else:
            cnt = p.application_count
            p.app_pct = min(100.0, round((cnt / p.max_candidates * 100), 1)) if p.max_candidates else 0
            p.is_full = cnt >= p.max_candidates

    filter_fields = [
        {
            "name": "q",
            "label": "Tìm kiếm",
            "placeholder": "Tên đợt thi...",
            "value": q,
        },
        {
            "name": "status",
            "label": "Trạng thái",
            "placeholder": "Tất cả trạng thái",
            "value": status_filter,
            "options": [
                {"value": "draft", "label": "Chưa công bố"},
                {"value": "published", "label": "Đã công bố"},
                {"value": "closed", "label": "Đã đóng"},
            ],
        },
    ]

    return render(request, "training/exam_period_list.html", {
        "active_menu": "exam-periods",
        "page_title": "Danh sách đợt thi",
        "page_description": "Quản lý kế hoạch tổ chức, tiến độ đăng ký và trạng thái các đợt thi.",
        "breadcrumbs": [{"label": "Đợt thi"}],
        "filter_fields": filter_fields,
        "page_obj": page_obj,
        "total_periods": total_periods,
        "draft_count": draft_count,
        "published_count": published_count,
        "closed_count": closed_count,
    })


@login_required
def exam_period_add(request):
    if not user_has_feature(request.user, "exam-periods"):
        raise PermissionDenied

    if request.method == "POST":
        form = ExamPeriodForm(request.POST)
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.full_clean()
                obj.save()
                record_audit(
                    request.user,
                    AuditLog.Action.CREATE,
                    obj,
                    new_values=snapshot_instance(obj),
                )
                messages.success(request, f"Đã thêm đợt thi '{obj.name}'.")
                return redirect("exam-period-list")
            except ValidationError as exc:
                form._update_errors(exc)
    else:
        form = ExamPeriodForm()

    return render(request, "training/exam_period_form.html", {
        "active_menu": "exam-periods",
        "page_title": "Thêm đợt thi",
        "breadcrumbs": [
            {"label": "Đợt thi", "url": reverse("exam-period-list")},
            {"label": "Thêm mới"},
        ],
        "form": form,
        "submit_label": "Thêm đợt thi",
        "cancel_url": reverse("exam-period-list"),
        "is_add": True,
    })


@login_required
def exam_period_edit(request, pk):
    if not user_has_feature(request.user, "exam-periods"):
        raise PermissionDenied

    obj = get_object_or_404(ExamPeriod, pk=pk)

    if obj.status == ExamPeriod.Status.CLOSED:
        messages.error(request, f"Đợt thi '{obj.name}' đã đóng, không thể chỉnh sửa thông tin.")
        return redirect("exam-period-list")

    if request.method == "POST":
        form = ExamPeriodForm(request.POST, instance=obj)
        if form.is_valid():
            try:
                old_values = snapshot_instance(ExamPeriod.objects.get(pk=pk))
                updated = form.save(commit=False)
                updated.full_clean()
                updated.save()
                if form.changed_data:
                    record_audit(
                        request.user,
                        AuditLog.Action.EDIT,
                        updated,
                        old_values=old_values,
                        new_values=snapshot_instance(updated),
                    )
                messages.success(request, f"Đã cập nhật đợt thi '{obj.name}'.")
                return redirect("exam-period-list")
            except ValidationError as exc:
                form._update_errors(exc)
    else:
        form = ExamPeriodForm(instance=obj)

    return render(request, "training/exam_period_form.html", {
        "active_menu": "exam-periods",
        "page_title": f"Sửa đợt thi: {obj.name}",
        "breadcrumbs": [
            {"label": "Đợt thi", "url": reverse("exam-period-list")},
            {"label": "Sửa"},
        ],
        "form": form,
        "submit_label": "Lưu thay đổi",
        "cancel_url": reverse("exam-period-list"),
        "obj": obj,
    })


@login_required
@require_POST
def exam_period_toggle(request, pk):
    if not user_has_feature(request.user, "exam-periods"):
        raise PermissionDenied

    obj = get_object_or_404(ExamPeriod, pk=pk)
    action = request.POST.get("action", "")
    old_status = obj.status

    if action == "publish" and obj.status == ExamPeriod.Status.DRAFT:
        obj.status = ExamPeriod.Status.PUBLISHED
        label = "công bố"
        audit_action = AuditLog.Action.PUBLISH
    elif action == "close" and obj.status == ExamPeriod.Status.PUBLISHED:
        obj.status = ExamPeriod.Status.CLOSED
        label = "đóng"
        audit_action = AuditLog.Action.REVOKE
    else:
        messages.error(request, "Thao tác không hợp lệ.")
        return redirect("exam-period-list")

    obj.save(update_fields=["status"])
    record_audit(
        request.user,
        audit_action,
        obj,
        old_values={"status": old_status},
        new_values={"status": obj.status},
        reason=request.POST.get("reason", ""),
    )
    messages.success(request, f"Đã {label} đợt thi '{obj.name}'.")
    return redirect("exam-period-list")


# ── Hồ sơ đăng ký dự thi ─────────────────────────────────────────────────────

@login_required
def exam_application_list(request):
    if not user_has_feature(request.user, "exam-applications"):
        raise PermissionDenied

    form = ExamApplicationFilterForm(request.GET or None)
    qs = ExamApplication.objects.select_related("exam_period")

    if form.is_valid():
        q = form.cleaned_data.get("q", "")
        period = form.cleaned_data.get("exam_period")
        source_val = form.cleaned_data.get("source", "")
        status_val = form.cleaned_data.get("status", "")
        if q:
            from django.db.models import Q as _Q
            qs = qs.filter(_Q(full_name__icontains=q) | _Q(id_number__icontains=q))
        if period:
            qs = qs.filter(exam_period=period)
        if source_val:
            qs = qs.filter(source=source_val)
        if status_val:
            qs = qs.filter(status=status_val)

    # Thống kê KPI toàn hệ thống
    total_applications = ExamApplication.objects.count()
    valid_count = ExamApplication.objects.filter(status=ExamApplication.Status.VALID).count()

    # Tìm các nhóm hồ sơ trùng lặp: cùng đợt thi, cùng số CCCD/CMND, họ tên và số điện thoại
    from django.db.models import Count
    dup_group_tuples = (
        ExamApplication.objects
        .values("exam_period", "id_number", "full_name", "phone")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )
    duplicate_count = sum(g["cnt"] - 1 for g in dup_group_tuples)
    new_count = ExamApplication.objects.filter(status=ExamApplication.Status.NEW).count()
    open_periods_count = ExamPeriod.objects.filter(status=ExamPeriod.Status.PUBLISHED).count()

    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(request, "training/exam_application_list.html", {
        "active_menu": "exam-applications",
        "page_title": "Hồ sơ đăng ký dự thi",
        "page_description": "Quản lý danh sách hồ sơ thí sinh đăng ký, sàng lọc hồ sơ hợp lệ và xử lý trùng lặp.",
        "breadcrumbs": [{"label": "Hồ sơ đăng ký dự thi"}],
        "filter_form": form,
        "page_obj": page_obj,
        "total_applications": total_applications,
        "valid_count": valid_count,
        "duplicate_count": duplicate_count,
        "new_count": new_count,
        "open_periods_count": open_periods_count,
    })


@login_required
def exam_application_import(request):
    if not user_has_feature(request.user, "exam-applications"):
        raise PermissionDenied

    exam_periods = ExamPeriod.objects.filter(
        status=ExamPeriod.Status.PUBLISHED
    ).order_by("-exam_date")
    selected_period = None
    selected_period_id = request.GET.get("period")
    if selected_period_id:
        try:
            selected_period = exam_periods.filter(pk=selected_period_id).first()
        except (TypeError, ValueError):
            selected_period = None

    if request.method == "POST":
        uploaded = request.FILES.get("file")
        if not uploaded:
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "valid_count": 0,
                "duplicate_count": 0,
                "preview_rows": [],
                "errors": [{"row": 0, "msg": "Vui lòng chọn file CSV."}],
            })

        period_id = request.POST.get("exam_period")
        try:
            period = ExamPeriod.objects.get(pk=period_id, status=ExamPeriod.Status.PUBLISHED)
        except (ExamPeriod.DoesNotExist, TypeError, ValueError):
            errors = [{"row": 0, "msg": "Đợt thi không hợp lệ hoặc chưa công bố."}]
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "valid_count": 0,
                "duplicate_count": 0,
                "preview_rows": [],
                "errors": errors,
            })

        to_create, errors, preview_rows = _parse_exam_application_csv(uploaded, period)
        duplicate_count = sum(
            application.status == ExamApplication.Status.DUPLICATE
            for application in to_create
        )
        if request.POST.get("preview") == "1":
            return JsonResponse({
                "ok": not errors,
                "saved": 0,
                "valid_count": len(to_create),
                "duplicate_count": duplicate_count,
                "preview_rows": preview_rows,
                "errors": errors,
            })

        if errors:
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "valid_count": len(to_create),
                "duplicate_count": duplicate_count,
                "preview_rows": preview_rows,
                "errors": errors,
            })

        with transaction.atomic():
            ExamApplication.objects.bulk_create(to_create)
            # Ghi audit tổng hợp (1 bản ghi thay vì N)
            record_audit(
                request.user,
                AuditLog.Action.CREATE,
                period,
                new_values={
                    "imported_count": len(to_create),
                    "duplicate_count": duplicate_count,
                    "source": "Google Form",
                },
                object_label=f"Nhập {len(to_create)} hồ sơ vào đợt thi {period.name}",
            )

        return JsonResponse({
            "ok": True,
            "saved": len(to_create),
            "duplicate_count": duplicate_count,
            "errors": [],
        })

    return render(request, "training/exam_application_import.html", {
        "active_menu": "exam-applications",
        "page_title": "Nhập danh sách từ Google Form",
        "breadcrumbs": [
            {"label": "Hồ sơ đăng ký dự thi", "url": reverse("exam-application-list")},
            {"label": "Nhập từ Google Form"},
        ],
        "exam_periods": exam_periods,
        "selected_period": selected_period,
        "import_url": reverse("exam-application-import"),
    })


def _csv_download(rows, filename):
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _parse_exam_application_csv(uploaded, period):
    try:
        text = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [{
            "row": 0,
            "msg": "File không đúng định dạng UTF-8. Vui lòng lưu lại dưới định dạng CSV UTF-8.",
        }], []

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames:
        reader.fieldnames = [
            field.strip() if field else ""
            for field in reader.fieldnames
        ]
    required_fields = {"Họ và tên", "Số CCCD/CMND"}
    missing = required_fields - set(reader.fieldnames or [])
    if missing:
        return [], [{
            "row": 0,
            "msg": (
                f"File thiếu cột: {', '.join(sorted(missing))}. "
                "Cần có Họ và tên, Số CCCD/CMND; Số điện thoại có thể để trống."
            ),
        }], []

    try:
        rows = list(reader)
    except csv.Error:
        return [], [{"row": 0, "msg": "File CSV bị lỗi định dạng."}], []
    if not rows:
        return [], [{"row": 0, "msg": "File CSV không có dòng dữ liệu."}], []

    errors = []
    to_create = []
    preview_rows = []
    existing_application_keys = set(
        period.applications.values_list("id_number", "full_name", "phone")
    )
    seen_application_keys = set()
    for row_number, row in enumerate(rows, start=2):
        row_period_name = (row.get("Tên đợt thi") or "").strip()
        full_name = (row.get("Họ và tên") or "").strip()
        id_number = (row.get("Số CCCD/CMND") or "").strip()
        phone = (row.get("Số điện thoại") or "").strip()

        if row_period_name and row_period_name != period.name:
            errors.append({
                "row": row_number,
                "msg": f"Tên đợt thi trong file không khớp với đợt đã chọn ({period.name}).",
            })
            continue
        if not full_name:
            errors.append({"row": row_number, "msg": "Thiếu họ và tên."})
            continue
        if not id_number:
            errors.append({"row": row_number, "msg": "Thiếu số CCCD/CMND."})
            continue
        if not id_number.isdigit() or len(id_number) not in (9, 12):
            errors.append({
                "row": row_number,
                "msg": f"Số CCCD/CMND '{id_number}' không hợp lệ (phải gồm 9 hoặc 12 chữ số).",
            })
            continue

        application_key = (id_number, full_name, phone)
        is_duplicate = (
            application_key in existing_application_keys
            or application_key in seen_application_keys
        )
        seen_application_keys.add(application_key)
        to_create.append(ExamApplication(
            exam_period=period,
            full_name=full_name,
            phone=phone,
            id_number=id_number,
            source=ExamApplication.Source.GOOGLE_FORM,
            status=(
                ExamApplication.Status.DUPLICATE
                if is_duplicate
                else ExamApplication.Status.VALID
            ),
        ))
        preview_rows.append({
            "row": row_number,
            "full_name": full_name,
            "id_number": id_number,
            "status": (
                ExamApplication.Status.DUPLICATE
                if is_duplicate
                else ExamApplication.Status.VALID
            ),
        })

    if not errors:
        current_application_count = len(existing_application_keys)
        new_application_count = sum(
            application.status == ExamApplication.Status.VALID
            for application in to_create
        )
        if (
            period.max_candidates
            and current_application_count + new_application_count > period.max_candidates
        ):
            errors.append({
                "row": 0,
                "msg": (
                    f"Số hồ sơ mới nhập ({new_application_count}) cộng với số hiện có "
                    f"({current_application_count}) vượt quá chỉ tiêu tối đa của đợt thi "
                    f"({period.max_candidates})."
                ),
            })

    return to_create, errors, preview_rows


@login_required
def exam_application_deduplicate(request):
    if not user_has_feature(request.user, "exam-applications"):
        raise PermissionDenied

    if request.method == "POST":
        # 1. Thao tác xóa từng bản ghi được chọn
        delete_id = request.POST.get("delete_id")
        if delete_id:
            del_app = get_object_or_404(ExamApplication, pk=delete_id)
            siblings = ExamApplication.objects.filter(
                exam_period=del_app.exam_period,
                id_number=del_app.id_number,
                full_name=del_app.full_name,
                phone=del_app.phone,
            ).exclude(pk=del_app.pk)

            with transaction.atomic():
                # Chuyển phòng thi/điểm thi (nếu có) sang hồ sơ còn lại để bảo toàn dữ liệu
                other_app = siblings.filter(status=ExamApplication.Status.VALID).first() or siblings.first()
                if other_app:
                    if other_app.status != ExamApplication.Status.VALID:
                        other_app.status = ExamApplication.Status.VALID
                        other_app.save(update_fields=["status"])
                    for assignment in list(del_app.room_assignments.all()):
                        if not other_app.room_assignments.filter(exam_room=assignment.exam_room).exists():
                            assignment.application = other_app
                            assignment.save(update_fields=["application"])
                        else:
                            keep_asg = other_app.room_assignments.filter(exam_room=assignment.exam_room).first()
                            if hasattr(assignment, "score") and not hasattr(keep_asg, "score"):
                                score = assignment.score
                                score.assignment = keep_asg
                                score.save(update_fields=["assignment"])
                            elif hasattr(assignment, "score"):
                                assignment.score.delete()
                            assignment.delete()

                record_audit(
                    request.user, AuditLog.Action.DELETE, del_app,
                    old_values={
                        "full_name": del_app.full_name,
                        "id_number": del_app.id_number,
                        "exam_period_id": del_app.exam_period_id,
                        "phone": del_app.phone,
                        "source": del_app.source,
                    },
                    reason=f"Xóa bản ghi hồ sơ trùng #{del_app.pk}",
                )
                del_name = del_app.full_name
                del_cccd = del_app.id_number
                del_app.delete()

            messages.success(request, f"Đã xóa thành công 1 bản ghi hồ sơ trùng của thí sinh '{del_name}' (CCCD: {del_cccd}).")
            return redirect("exam-application-deduplicate")

        # 2. Thao tác giữ lại 1 hồ sơ và xóa các bản ghi trùng của riêng nhóm thí sinh đó
        keep_id = request.POST.get("keep_id")
        if not keep_id:
            for k, v in request.POST.items():
                if k.startswith("keep_id") and v:
                    keep_id = v
                    break

        if not keep_id:
            messages.error(request, "Vui lòng chọn hồ sơ cần giữ lại.")
            return redirect("exam-application-deduplicate")

        keep_app = get_object_or_404(ExamApplication, pk=keep_id)
        siblings = ExamApplication.objects.filter(
            exam_period=keep_app.exam_period,
            id_number=keep_app.id_number,
            full_name=keep_app.full_name,
            phone=keep_app.phone,
        ).exclude(pk=keep_app.pk)

        if not siblings.exists():
            messages.info(request, f"Hồ sơ của thí sinh '{keep_app.full_name}' không còn bản ghi trùng lặp nào.")
            return redirect("exam-application-deduplicate")

        with transaction.atomic():
            if keep_app.status != ExamApplication.Status.VALID:
                old_status = keep_app.status
                keep_app.status = ExamApplication.Status.VALID
                keep_app.save(update_fields=["status"])
                record_audit(
                    request.user, AuditLog.Action.EDIT, keep_app,
                    old_values={"status": old_status},
                    new_values={"status": keep_app.status},
                    reason="Giữ lại trong quá trình xử lý trùng",
                )

            del_count = siblings.count()
            for sib in siblings:
                for assignment in list(sib.room_assignments.all()):
                    if not keep_app.room_assignments.filter(exam_room=assignment.exam_room).exists():
                        assignment.application = keep_app
                        assignment.save(update_fields=["application"])
                    else:
                        keep_asg = keep_app.room_assignments.filter(exam_room=assignment.exam_room).first()
                        if hasattr(assignment, "score") and not hasattr(keep_asg, "score"):
                            score = assignment.score
                            score.assignment = keep_asg
                            score.save(update_fields=["assignment"])
                        elif hasattr(assignment, "score"):
                            assignment.score.delete()
                        assignment.delete()

                record_audit(
                    request.user, AuditLog.Action.DELETE, sib,
                    old_values={
                        "full_name": sib.full_name,
                        "id_number": sib.id_number,
                        "exam_period_id": sib.exam_period_id,
                        "phone": sib.phone,
                        "source": sib.source,
                    },
                    reason=f"Xóa hồ sơ trùng lặp với hồ sơ #{keep_app.pk} ({keep_app.full_name})",
                )
                sib.delete()

        messages.success(request, f"Đã giữ lại hồ sơ #{keep_app.pk} của thí sinh '{keep_app.full_name}' và xóa {del_count} hồ sơ trùng lặp.")
        return redirect("exam-application-deduplicate")

    # GET: nhóm các hồ sơ trùng (phải trùng đợt thi, số CCCD/CMND, họ tên và số điện thoại)
    from django.db.models import Count
    duplicate_groups_qs = (
        ExamApplication.objects
        .values("exam_period", "id_number", "full_name", "phone")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
        .order_by("-cnt")
    )

    groups = []
    seen = set()
    for g in duplicate_groups_qs:
        key = (g["exam_period"], g["id_number"], g["full_name"], g["phone"])
        if key in seen:
            continue
        seen.add(key)
        members = ExamApplication.objects.filter(
            exam_period_id=g["exam_period"],
            id_number=g["id_number"],
            full_name=g["full_name"],
            phone=g["phone"],
        ).select_related("exam_period").prefetch_related("room_assignments__exam_room").order_by("-status", "registered_at")
        if members.count() > 1:
            groups.append({
                "id_number": g["id_number"],
                "full_name": g["full_name"],
                "phone": g["phone"],
                "exam_period": members.first().exam_period,
                "members": members,
            })

    page_obj = Paginator(groups, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(request, "training/exam_application_deduplicate.html", {
        "active_menu": "exam-applications",
        "page_title": "Xử lý hồ sơ trùng",
        "breadcrumbs": [
            {"label": "Hồ sơ đăng ký dự thi", "url": reverse("exam-application-list")},
            {"label": "Xử lý trùng"},
        ],
        "groups": page_obj,
        "page_obj": page_obj,
        "has_duplicates": bool(page_obj.paginator.count),
    })


# =============================================================================
# EXAM VIEWS — Pha 2: Phòng thi
# =============================================================================

from training.models import ExamRoom, ExamRoomAssignment, ExamScore


SESSION_CHOICES = [
    ("", "-- Chọn khung giờ ca thi --"),
    ("Ca 1 — Sáng (07:30–09:30)", "Ca 1 — Sáng (07:30–09:30)"),
    ("Ca 2 — Sáng (09:30–11:30)", "Ca 2 — Sáng (09:30–11:30)"),
    ("Ca 3 — Chiều (13:30–15:30)", "Ca 3 — Chiều (13:30–15:30)"),
    ("Ca 4 — Chiều (15:30–17:30)", "Ca 4 — Chiều (15:30–17:30)"),
    ("Ca 5 — Tối (18:00–20:00)", "Ca 5 — Tối (18:00–20:00)"),
    ("Ca 1 — Sáng (08:00–10:00)", "Ca 1 — Sáng (08:00–10:00)"),
    ("Ca 2 — Sáng (10:00–12:00)", "Ca 2 — Sáng (10:00–12:00)"),
    ("Ca 3 — Chiều (14:00–16:00)", "Ca 3 — Chiều (14:00–16:00)"),
    ("Ca 4 — Chiều (16:00–18:00)", "Ca 4 — Chiều (16:00–18:00)"),
    ("Ca 5 — Tối (18:30–20:30)", "Ca 5 — Tối (18:30–20:30)"),
]


class ExamRoomForm(django_forms.ModelForm):
    session = django_forms.CharField(
        label="Ca thi (Khung giờ)",
        widget=django_forms.Select(choices=SESSION_CHOICES, attrs={"class": "cce-select"}),
    )

    class Meta:
        model = ExamRoom
        fields = ["exam_period", "name", "session", "capacity"]
        widgets = {
            "exam_period": django_forms.Select(attrs={"class": "cce-select"}),
            "name": django_forms.TextInput(attrs={"class": "cce-input", "placeholder": "Ví dụ: Phòng A101, B202..."}),
            "capacity": django_forms.NumberInput(attrs={"class": "cce-input", "min": 1, "placeholder": "Ví dụ: 30"}),
        }

    def __init__(self, *args, **kwargs):
        self.exam_period_lock = kwargs.pop("exam_period_lock", None)
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not field.widget.attrs.get("class"):
                field.widget.attrs["class"] = "cce-input"

        if self.exam_period_lock:
            self.fields["exam_period"].required = False
            self.fields["exam_period"].initial = self.exam_period_lock
            self.fields["exam_period"].widget = django_forms.HiddenInput()
        else:
            self.fields["exam_period"].queryset = ExamPeriod.objects.filter(
                status=ExamPeriod.Status.PUBLISHED
            ).order_by("-exam_date")
            self.fields["exam_period"].empty_label = "-- Chọn đợt thi --"


@login_required
def exam_room_list(request):
    """Danh sách phòng thi — lọc theo đợt thi."""
    if not user_has_feature(request.user, "exam-rooms"):
        raise PermissionDenied

    # Xử lý tạo phòng thi nhanh qua Modal tại trang danh sách
    if request.method == "POST" and "create_room" in request.POST:
        period_id = request.POST.get("exam_period")
        target_period = get_object_or_404(ExamPeriod, pk=period_id)
        if target_period.status == ExamPeriod.Status.DRAFT:
            messages.error(request, "Không thể tạo phòng cho đợt thi chưa công bố.")
            return redirect("exam-room-list")
        if target_period.status == ExamPeriod.Status.CLOSED:
            messages.error(request, "Đợt thi đã đóng, không thể tạo thêm phòng thi.")
            return redirect("exam-room-list")

        form = ExamRoomForm(request.POST)
        if form.is_valid():
            new_room = form.save(commit=False)
            new_room.exam_period = target_period
            new_room.save()
            record_audit(
                request.user,
                AuditLog.Action.CREATE,
                new_room,
                object_label=f"Phòng {new_room.name} — {target_period.name}",
                new_values=snapshot_instance(new_room),
            )
            messages.success(request, f"Đã tạo phòng thi \"{new_room.name}\". Bạn có thể tiếp tục xếp thí sinh vào phòng bên dưới.")
            return redirect(reverse("exam-room-assign", args=[target_period.pk]) + f"?room_id={new_room.pk}")
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"{field}: {err}")

    periods = ExamPeriod.objects.filter(status=ExamPeriod.Status.PUBLISHED).order_by("-exam_date")
    all_periods = ExamPeriod.objects.order_by("-exam_date")
    period_id = request.GET.get("period", "")
    selected_period = None
    if period_id:
        selected_period = get_object_or_404(ExamPeriod, pk=period_id)

    qs = ExamRoom.objects.select_related("exam_period")
    if selected_period:
        qs = qs.filter(exam_period=selected_period)

    from django.db.models import Sum

    qs = qs.order_by("name")
    total_rooms = qs.count()
    total_capacity = qs.aggregate(total=Sum("capacity"))["total"] or 0
    total_assigned = ExamRoomAssignment.objects.filter(exam_room__in=qs).count()
    total_remaining = max(0, total_capacity - total_assigned)
    fill_rate = round((total_assigned / total_capacity * 100), 1) if total_capacity else 0

    page_obj = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    rooms_data = []
    for room in page_obj:
        assigned = room.room_assignments.count()
        remaining = max(0, room.capacity - assigned)
        pct = round((assigned / room.capacity * 100), 1) if room.capacity else 0
        rooms_data.append({
            "room": room,
            "assigned_count": assigned,
            "remaining": remaining,
            "pct": pct,
        })

    # Tính tổng số thí sinh hợp lệ chưa được xếp phòng
    all_assigned_apps = ExamRoomAssignment.objects.values_list("application_id", flat=True)
    unassigned_qs = ExamApplication.objects.filter(
        status=ExamApplication.Status.VALID,
        exam_period__status=ExamPeriod.Status.PUBLISHED,
    ).exclude(pk__in=all_assigned_apps)
    if selected_period:
        unassigned_qs = unassigned_qs.filter(exam_period=selected_period)
    total_unassigned = unassigned_qs.count()

    filter_fields = [
        {
            "name": "period",
            "label": "Đợt thi",
            "placeholder": "Tất cả đợt thi",
            "value": period_id,
            "options": [
                {"value": str(p.pk), "label": p.name} for p in all_periods
            ],
        },
    ]

    create_room_form = ExamRoomForm(
        initial={"capacity": 30, "exam_period": selected_period.pk if selected_period else None}
    )

    return render(request, "training/exam_room_list.html", {
        "active_menu": "exam-rooms",
        "page_title": "Danh sách phòng thi",
        "page_description": "Quản lý phòng thi và thí sinh trong từng phòng của mỗi đợt thi.",
        "breadcrumbs": [{"label": "Phòng thi"}],
        "filter_fields": filter_fields,
        "rooms_data": rooms_data,
        "page_obj": page_obj,
        "selected_period": selected_period,
        "periods": periods,
        "all_periods": all_periods,
        "total_rooms": total_rooms,
        "total_capacity": total_capacity,
        "total_assigned": total_assigned,
        "total_remaining": total_remaining,
        "total_unassigned": total_unassigned,
        "fill_rate": fill_rate,
        "create_room_form": create_room_form,
    })


@login_required
def exam_room_assign(request, pk):
    """
    Tạo phòng thi mới hoặc xem/sửa phòng thi đã có, bao gồm xếp thí sinh.
    pk = ExamPeriod.pk (tạo phòng mới cho đợt thi đó)
    Nếu request.GET có room_id thì đang xem/xếp phòng đã tạo.
    """
    if not user_has_feature(request.user, "exam-rooms"):
        raise PermissionDenied

    exam_period = get_object_or_404(ExamPeriod, pk=pk)
    if exam_period.status == ExamPeriod.Status.DRAFT:
        messages.error(request, "Đợt thi chưa công bố không thể quản lý hoặc tạo phòng thi.")
        return redirect("exam-period-list")

    rooms_in_period = ExamRoom.objects.filter(exam_period=exam_period).order_by("name")

    room_id = request.GET.get("room_id") or request.POST.get("room_id")
    room = get_object_or_404(ExamRoom, pk=room_id, exam_period=exam_period) if room_id else None

    # Tự động chọn phòng đầu tiên nếu chưa chỉ định room_id
    if not room and rooms_in_period.exists():
        room = rooms_in_period.first()

    # --- Xử lý tạo phòng ---
    if request.method == "POST" and "create_room" in request.POST:
        form = ExamRoomForm(request.POST, exam_period_lock=exam_period)
        if form.is_valid():
            new_room = form.save(commit=False)
            new_room.exam_period = exam_period
            new_room.save()
            record_audit(
                request.user,
                AuditLog.Action.CREATE,
                new_room,
                object_label=f"Phòng {new_room.name} — {exam_period.name}",
                new_values=snapshot_instance(new_room),
            )
            messages.success(request, f"Đã tạo phòng thi \"{new_room.name}\". Tiếp tục xếp thí sinh vào phòng bên dưới.")
            return redirect(
                reverse("exam-room-assign", args=[exam_period.pk])
                + f"?room_id={new_room.pk}"
            )
        else:
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f"{field}: {err}")
    else:
        form = ExamRoomForm(exam_period_lock=exam_period, initial={"capacity": 30})

    # --- Xử lý tự động xếp thí sinh (Lấp đầy phòng) ---
    if request.method == "POST" and "auto_fill" in request.POST and room:
        remaining = max(0, room.capacity - room.room_assignments.count())
        if remaining <= 0:
            messages.warning(request, f"Phòng \"{room.name}\" đã đủ sức chứa ({room.capacity} thí sinh).")
        else:
            already_assigned = ExamRoomAssignment.objects.filter(
                exam_room__exam_period=exam_period
            ).values_list("application_id", flat=True)
            candidates = list(
                ExamApplication.objects.filter(
                    exam_period=exam_period,
                    status=ExamApplication.Status.VALID,
                ).exclude(pk__in=already_assigned).order_by("registered_at", "full_name")[:remaining]
            )
            if not candidates:
                messages.info(request, "Không còn thí sinh hợp lệ nào đang chờ xếp phòng trong đợt này.")
            else:
                to_assign = [ExamRoomAssignment(exam_room=room, application=c) for c in candidates]
                with transaction.atomic():
                    ExamRoomAssignment.objects.bulk_create(to_assign)
                    record_audit(
                        request.user,
                        AuditLog.Action.CREATE,
                        room,
                        object_label=f"Tự động xếp {len(to_assign)} thí sinh vào phòng {room.name}",
                        new_values={"count": len(to_assign), "room_id": room.pk},
                    )
                messages.success(request, f"Đã tự động xếp {len(to_assign)} thí sinh vào phòng \"{room.name}\".")
        return redirect(
            reverse("exam-room-assign", args=[exam_period.pk])
            + f"?room_id={room.pk}"
        )

    # --- Xử lý xếp thí sinh theo checkbox ---
    assign_errors = []
    if request.method == "POST" and "assign_candidates" in request.POST and room:
        app_ids = request.POST.getlist("app_ids")
        if not app_ids:
            messages.warning(request, "Vui lòng chọn ít nhất 1 thí sinh để xếp vào phòng.")
            return redirect(reverse("exam-room-assign", args=[exam_period.pk]) + f"?room_id={room.pk}")

        assigned_count = 0
        already_in_room = 0
        for app_id in app_ids:
            app = get_object_or_404(
                ExamApplication, pk=app_id, exam_period=exam_period,
                status=ExamApplication.Status.VALID,
            )
            already = ExamRoomAssignment.objects.filter(
                application=app,
                exam_room__exam_period=exam_period,
            ).exists()
            if already:
                already_in_room += 1
                continue
            current = room.room_assignments.count()
            if current >= room.capacity:
                assign_errors.append(f"Phòng đã đủ sức chứa ({room.capacity} thí sinh).")
                break
            with transaction.atomic():
                assignment = ExamRoomAssignment.objects.create(
                    exam_room=room, application=app,
                )
                record_audit(
                    request.user,
                    AuditLog.Action.CREATE,
                    assignment,
                    object_label=f"Xếp {app.full_name} vào {room.name}",
                    new_values=snapshot_instance(assignment),
                )
            assigned_count += 1
        if assigned_count:
            messages.success(request, f"Đã xếp {assigned_count} thí sinh vào phòng \"{room.name}\".")
        if already_in_room:
            messages.warning(request, f"{already_in_room} thí sinh đã được xếp phòng khác, bỏ qua.")
        if assign_errors:
            for err in assign_errors:
                messages.error(request, err)
        return redirect(
            reverse("exam-room-assign", args=[exam_period.pk])
            + f"?room_id={room.pk}"
        )

    # --- Xử lý bỏ thí sinh khỏi phòng ---
    if request.method == "POST" and "remove_candidate" in request.POST and room:
        removal_error = None
        candidate_name = ""
        with transaction.atomic():
            locked_period = ExamPeriod.objects.select_for_update().get(pk=exam_period.pk)
            if locked_period.status == ExamPeriod.Status.CLOSED:
                removal_error = "Đợt thi đã đóng, không thể bỏ thí sinh khỏi phòng."
            elif locked_period.status != ExamPeriod.Status.PUBLISHED:
                removal_error = "Đợt thi chưa công bố, không thể bỏ thí sinh khỏi phòng."
            else:
                assignment = get_object_or_404(
                    ExamRoomAssignment.objects.select_for_update().select_related("application"),
                    pk=request.POST.get("assignment_id"),
                    exam_room=room,
                )
                score = ExamScore.objects.select_for_update().filter(
                    assignment=assignment
                ).first()
                if score and score.blocks_room_removal:
                    removal_error = (
                        "Thí sinh đã có điểm, được ghi nhận vắng thi hoặc kết quả đã công bố; "
                        "không thể bỏ khỏi phòng."
                    )
                else:
                    candidate_name = assignment.application.full_name
                    assignment_snapshot = snapshot_instance(assignment)
                    if score:
                        score.delete()
                    assignment.delete()
                    record_audit(
                        request.user,
                        AuditLog.Action.DELETE,
                        room,
                        object_label=f"Bỏ {candidate_name} khỏi {room.name}",
                        old_values=assignment_snapshot,
                    )

        if removal_error:
            messages.error(request, removal_error)
        else:
            messages.success(request, f"Đã bỏ \"{candidate_name}\" khỏi phòng \"{room.name}\".")
        return redirect(
            reverse("exam-room-assign", args=[exam_period.pk])
            + f"?room_id={room.pk}"
        )

    # Thí sinh hiện tại trong phòng
    assignments_in_room = []
    if room:
        assignments_in_room = room.room_assignments.select_related(
            "application", "score"
        ).order_by("application__full_name")
    rooms_page_obj = Paginator(rooms_in_period, PAGE_SIZE).get_page(
        request.GET.get("rooms_page")
    )
    assigned_page_obj = Paginator(assignments_in_room, PAGE_SIZE).get_page(
        request.GET.get("assigned_page")
    )

    # Thí sinh hợp lệ chưa được xếp phòng (trong đợt thi này)
    assigned_app_ids = ExamRoomAssignment.objects.filter(
        exam_room__exam_period=exam_period
    ).values_list("application_id", flat=True)

    unassigned_candidates = ExamApplication.objects.filter(
        exam_period=exam_period,
        status=ExamApplication.Status.VALID,
    ).exclude(pk__in=assigned_app_ids).order_by("registered_at", "full_name")

    # Phân trang danh sách thí sinh chờ xếp: 10 thí sinh / trang
    unassigned_paginator = Paginator(unassigned_candidates, PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    unassigned_page_obj = unassigned_paginator.get_page(page_number)

    # Thí sinh không đủ điều kiện (trùng hoặc mới)
    ineligible_candidates = ExamApplication.objects.filter(
        exam_period=exam_period,
    ).exclude(status=ExamApplication.Status.VALID).order_by("full_name")
    ineligible_page_obj = Paginator(ineligible_candidates, PAGE_SIZE).get_page(
        request.GET.get("ineligible_page")
    )

    return render(request, "training/exam_room_assign.html", {
        "active_menu": "exam-rooms",
        "page_title": f"Phòng thi — {exam_period.name}",
        "page_description": "Tạo phòng thi và xếp thí sinh.",
        "breadcrumbs": [
            {"label": "Phòng thi", "url": reverse("exam-room-list")},
            {"label": exam_period.name},
        ],
        "exam_period": exam_period,
        "form": form,
        "room": room,
        "rooms_in_period": rooms_page_obj,
        "rooms_page_obj": rooms_page_obj,
        "assignments_in_room": assigned_page_obj,
        "assigned_page_obj": assigned_page_obj,
        "unassigned_candidates": unassigned_candidates,
        "unassigned_page_obj": unassigned_page_obj,
        "page_size": PAGE_SIZE,
        "ineligible_candidates": ineligible_page_obj,
        "ineligible_page_obj": ineligible_page_obj,
        "assign_errors": assign_errors,
    })

# =============================================================================
# EXAM VIEWS — Pha 3: Điểm thi
# =============================================================================

import csv
import io
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from training.models import ExamRoom, ExamRoomAssignment, ExamScore


# ── Danh sách điểm thi ───────────────────────────────────────────────────────

@login_required
def exam_score_list(request):
    """Danh sách điểm thi — lọc theo đợt thi và phòng thi."""
    if not user_has_feature(request.user, "exam-scores"):
        raise PermissionDenied

    # Xử lý nhập điểm thủ công cho từng thí sinh qua popup
    if request.method == "POST" and "save_single_score" in request.POST:
        assignment_id = request.POST.get("assignment_id")
        asgn = get_object_or_404(
            ExamRoomAssignment.objects.select_related("application", "exam_room__exam_period"),
            pk=assignment_id,
        )
        score_scale = asgn.exam_room.exam_period.score_scale
        score_obj, _ = ExamScore.objects.get_or_create(assignment=asgn)
        old_snap = snapshot_instance(score_obj)

        is_absent = request.POST.get("is_absent") == "1"
        raw_score = request.POST.get("score", "").strip()
        reason = request.POST.get("reason", "").strip()

        if score_obj.is_locked and not reason:
            messages.error(request, "Điểm của thí sinh này đã được chốt. Vui lòng nhập lý do điều chỉnh bắt buộc.")
            return redirect(request.POST.get("redirect_url") or request.get_full_path())

        if is_absent:
            score_obj.score = None
            score_obj.is_absent = True
            score_obj.entered_at = timezone.now()
            score_obj.save()
            action_label = f"Đánh dấu vắng thi cho thí sinh '{asgn.application.full_name}'"
        elif not is_absent:
            try:
                val = Decimal(raw_score.replace(",", "."))
                if val < 0 or val > score_scale:
                    messages.error(request, f"Điểm không hợp lệ: phải từ 0 đến thang điểm tối đa {score_scale}.")
                    return redirect(request.POST.get("redirect_url") or request.get_full_path())
                score_obj.score = val
                score_obj.is_absent = False
                score_obj.entered_at = timezone.now()
                score_obj.save()
                action_label = f"Nhập điểm {val}/{score_scale} cho thí sinh '{asgn.application.full_name}'"
            except (InvalidOperation, ValueError):
                messages.error(request, "Định dạng điểm số không hợp lệ.")
                return redirect(request.POST.get("redirect_url") or request.get_full_path())
        else:
            score_obj.score = None
            score_obj.is_absent = False
            score_obj.save()
            action_label = f"Xóa điểm của thí sinh '{asgn.application.full_name}'"

        record_audit(
            request.user,
            AuditLog.Action.EDIT,
            score_obj,
            object_label=f"{action_label} (Phòng {asgn.exam_room.name})",
            old_values=old_snap,
            new_values=snapshot_instance(score_obj),
            reason=reason if score_obj.is_locked else "",
        )
        messages.success(request, f"Đã lưu điểm cho thí sinh \"{asgn.application.full_name}\" thành công.")
        return redirect(request.POST.get("redirect_url") or request.get_full_path())

    periods = ExamPeriod.objects.order_by("-exam_date")
    period_id = request.GET.get("period", "")
    room_id = request.GET.get("room", "")

    selected_period = None
    selected_room = None
    if period_id:
        selected_period = get_object_or_404(ExamPeriod, pk=period_id)
    if room_id:
        selected_room = get_object_or_404(ExamRoom, pk=room_id)

    # Base queryset: ExamScore, join assignment → application
    qs = ExamRoomAssignment.objects.select_related(
        "application", "exam_room", "exam_room__exam_period"
    )
    if selected_period:
        qs = qs.filter(exam_room__exam_period=selected_period)
    if selected_room:
        qs = qs.filter(exam_room=selected_room)

    # Annotate with score info
    rows = []
    total = qs.count()
    has_score_count = 0
    passed_count = 0
    failed_count = 0
    absent_count = 0
    published_count = 0

    for asgn in qs.order_by("exam_room__name", "application__full_name"):
        score_obj = getattr(asgn, "score", None)
        try:
            score_obj = asgn.score
        except ExamScore.DoesNotExist:
            score_obj = None

        if score_obj and (score_obj.score is not None or score_obj.is_absent):
            has_score_count += 1
            if score_obj.is_absent:
                absent_count += 1
            if score_obj.is_published:
                published_count += 1

        # Determine pass/fail
        passed = None
        if score_obj and score_obj.score is not None and not score_obj.is_absent:
            scale = asgn.exam_room.exam_period.score_scale
            passed = score_obj.score >= (scale * Decimal("0.5"))
            if passed:
                passed_count += 1
            else:
                failed_count += 1

        rows.append({
            "assignment": asgn,
            "score_obj": score_obj,
            "passed": passed,
        })

    score_progress_pct = round(has_score_count / total * 100, 1) if total else 0
    scored_candidates = passed_count + failed_count
    pass_rate = round(passed_count / scored_candidates * 100, 1) if scored_candidates else 0

    rooms_for_filter = []
    if selected_period:
        rooms_for_filter = ExamRoom.objects.filter(exam_period=selected_period)

    filter_fields = [
        {
            "name": "period",
            "label": "Đợt thi",
            "placeholder": "Tất cả đợt thi",
            "value": period_id,
            "options": [{"value": str(p.pk), "label": p.name} for p in periods],
        },
        {
            "name": "room",
            "label": "Phòng thi",
            "placeholder": "Tất cả phòng",
            "value": room_id,
            "options": [{"value": str(r.pk), "label": r.name} for r in rooms_for_filter],
        },
    ]

    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(request, "training/exam_score_list.html", {
        "active_menu": "exam-scores",
        "page_title": "Danh sách điểm thi",
        "page_description": "Xem, nhập và quản lý điểm thi theo từng phòng và đợt thi.",
        "breadcrumbs": [{"label": "Điểm thi"}],
        "filter_fields": filter_fields,
        "page_obj": page_obj,
        "total": total,
        "has_score_count": has_score_count,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "absent_count": absent_count,
        "published_count": published_count,
        "score_progress_pct": score_progress_pct,
        "pass_rate": pass_rate,
        "selected_period": selected_period,
        "selected_room": selected_room,
    })


# ── Nhập điểm thủ công (entry + chốt) ────────────────────────────────────────

@login_required
def exam_score_entry(request, room_id):
    """
    Nhập điểm tay từng thí sinh trong một phòng thi.
    Nút "Chốt điểm" khoá bảng điểm; sửa sau khi chốt cần lý do.
    """
    if not user_has_feature(request.user, "exam-scores"):
        raise PermissionDenied

    room = get_object_or_404(ExamRoom, pk=room_id)
    all_assignments = room.room_assignments.select_related("application").order_by(
        "application__full_name"
    )
    score_scale = room.exam_period.score_scale

    # Ensure ExamScore rows exist for every assignment
    for asgn in all_assignments:
        ExamScore.objects.get_or_create(assignment=asgn)

    all_assignments = room.room_assignments.select_related(
        "application", "score"
    ).order_by("application__full_name")
    page_obj = Paginator(all_assignments, PAGE_SIZE).get_page(request.GET.get("page"))
    assignments = list(page_obj)

    errors = {}
    reason_error = None

    if request.method == "POST":
        action = request.POST.get("action", "save")

        # Sửa điểm sau khi chốt — bắt buộc lý do
        is_locked = all(
            getattr(getattr(a, "score", None), "is_locked", False)
            for a in all_assignments
        )
        if is_locked:
            reason = request.POST.get("reason", "").strip()
            if not reason:
                reason_error = "Vui lòng nhập lý do chỉnh sửa điểm đã chốt."

        if not reason_error:
            # Save scores
            with transaction.atomic():
                for asgn in assignments:
                    score_obj = asgn.score
                    old_snap = snapshot_instance(score_obj)

                    is_absent_key = f"absent_{asgn.pk}"
                    score_key = f"score_{asgn.pk}"
                    is_absent = request.POST.get(is_absent_key) == "1"
                    raw_score = request.POST.get(score_key, "").strip()

                    if is_absent:
                        score_obj.score = None
                        score_obj.is_absent = True
                        score_obj.entered_at = timezone.now()
                        score_obj.save()
                    elif raw_score:
                        try:
                            val = Decimal(raw_score.replace(",", "."))
                            if val < 0 or val > score_scale:
                                errors[asgn.pk] = f"Điểm phải từ 0 đến {score_scale}."
                                continue
                            score_obj.score = val
                            score_obj.is_absent = False
                            score_obj.entered_at = timezone.now()
                            score_obj.save()
                        except InvalidOperation:
                            errors[asgn.pk] = "Điểm không hợp lệ."
                            continue
                    else:
                        # Clear
                        score_obj.score = None
                        score_obj.is_absent = False
                        score_obj.save()

                    new_snap = snapshot_instance(score_obj)
                    if old_snap != new_snap:
                        note = request.POST.get("reason", "") if is_locked else ""
                        record_audit(
                            request.user,
                            AuditLog.Action.EDIT,
                            score_obj,
                            object_label=f"Điểm {asgn.application.full_name} — {room.name}",
                            old_values=old_snap,
                            new_values=new_snap,
                            reason=note,
                        )

                # Chốt điểm
                if action == "lock" and not errors:
                    lock_count = 0
                    for asgn in all_assignments:
                        score_obj = asgn.score
                        score_obj.is_locked = True
                        score_obj.save(update_fields=["is_locked"])
                        lock_count += 1
                    record_audit(
                        request.user,
                        AuditLog.Action.EDIT,
                        room,
                        object_label=f"Chốt điểm phòng {room.name}",
                        new_values={"locked": True, "count": lock_count},
                    )
                    messages.success(request, f"Đã chốt điểm phòng \"{room.name}\".")
                    return redirect(
                        f"{reverse('exam-score-entry', args=[room.pk])}?page={page_obj.number}"
                    )

            if not errors:
                messages.success(request, "Đã lưu điểm thành công.")
                return redirect(
                    f"{reverse('exam-score-entry', args=[room.pk])}?page={page_obj.number}"
                )
            else:
                messages.error(request, f"Có {len(errors)} lỗi nhập điểm, vui lòng kiểm tra lại.")

    # Reload assignments with fresh scores
    all_assignments = room.room_assignments.select_related(
        "application", "score"
    ).order_by("application__full_name")
    page_obj = Paginator(all_assignments, PAGE_SIZE).get_page(request.GET.get("page"))
    assignments = list(page_obj)
    is_locked = all(
        getattr(getattr(a, "score", None), "is_locked", False)
        for a in all_assignments
    ) and all_assignments.exists()
    has_score_count = sum(
        1 for a in all_assignments
        if getattr(getattr(a, "score", None), "score", None) is not None
        or getattr(getattr(a, "score", None), "is_absent", False)
    )

    return render(request, "training/exam_score_entry.html", {
        "active_menu": "exam-scores",
        "page_title": f"Nhập điểm — {room.name}",
        "page_description": f"Nhập điểm thi cho phòng {room.name}, đợt {room.exam_period.name}.",
        "breadcrumbs": [
            {"label": "Điểm thi", "url": reverse("exam-score-list")},
            {"label": room.name},
        ],
        "room": room,
        "assignments": assignments,
        "page_obj": page_obj,
        "score_scale": score_scale,
        "errors": errors,
        "reason_error": reason_error,
        "is_locked": is_locked,
        "has_score_count": has_score_count,
        "total": all_assignments.count(),
    })


# ── Upload điểm thi (3-bước wizard) ─────────────────────────────────────────

@login_required
def exam_score_upload(request):
    """Upload file CSV điểm thi — dùng upload_wizard component."""
    if not user_has_feature(request.user, "exam-scores"):
        raise PermissionDenied

    periods = ExamPeriod.objects.order_by("-exam_date")
    selected_period_id = request.GET.get("period") or request.POST.get("period")
    selected_room_id = request.GET.get("room") or request.POST.get("room")

    selected_period = None
    selected_room = None
    rooms_for_period = []
    room_assignments_page_obj = None
    sample_available = False
    if selected_period_id:
        selected_period = get_object_or_404(periods, pk=selected_period_id)
        rooms_for_period = _score_upload_rooms().filter(exam_period=selected_period)
    if selected_room_id:
        selected_room = get_object_or_404(
            _score_upload_rooms().select_related("exam_period"), pk=selected_room_id
        )
        if selected_period and selected_room.exam_period_id != selected_period.pk:
            selected_room = None
        elif not selected_period:
            selected_period = selected_room.exam_period
            rooms_for_period = _score_upload_rooms().filter(exam_period=selected_period)

    selected_room_blocked = bool(
        selected_room
        and selected_room.has_published_scores
        and not (request.method == "POST" and request.FILES.get("file"))
    )
    if selected_room_blocked:
        selected_room = None
    if selected_room:
        room_assignments = selected_room.room_assignments.select_related(
            "application", "score"
        ).order_by("application__full_name")
        room_assignments_page_obj = Paginator(
            room_assignments, PAGE_SIZE
        ).get_page(request.GET.get("roster_page"))
        sample_available = selected_room.room_assignments.filter(score__isnull=True).exists()

    # Handle AJAX upload (step 1 — validate/preview)
    # Wizard JS sends preview=1 as a POST field (no custom header)
    _upload_step = request.headers.get("X-Upload-Step") or (
        "validate" if request.POST.get("preview") == "1" else
        ("confirm" if request.method == "POST" and request.FILES.get("file") and not request.POST.get("preview") else None)
    )
    if request.method == "POST" and _upload_step == "validate":
        return _score_upload_validate(request, selected_room)

    # Handle AJAX upload (step 2 — confirm & save)
    if request.method == "POST" and _upload_step == "confirm":
        return _score_upload_confirm(request, selected_room)

    upload_url = (
        reverse("exam-score-upload")
        + (
            f"?period={selected_period.pk}&room={selected_room.pk}"
            if selected_period and selected_room
            else ""
        )
    )

    return render(request, "training/exam_score_upload.html", {
        "active_menu": "exam-scores",
        "page_title": "Nhập điểm từ file",
        "page_description": (
            "Tải CSV đủ thông tin thí sinh, đợt thi, phòng, ca thi và điểm; "
            "hệ thống sẽ đối chiếu trước khi nhập."
        ),
        "breadcrumbs": [
            {"label": "Điểm thi", "url": reverse("exam-score-list")},
            {"label": "Nhập từ file"},
        ],
        "periods": periods,
        "selected_period": selected_period,
        "rooms_for_period": rooms_for_period,
        "selected_room": selected_room,
        "selected_room_blocked": selected_room_blocked,
        "room_assignments_page_obj": room_assignments_page_obj,
        "sample_available": sample_available,
        "upload_url": upload_url,
    })


def _score_upload_rooms():
    published_scores = ExamScore.objects.filter(
        assignment__exam_room_id=OuterRef("pk"),
        is_published=True,
    )
    return ExamRoom.objects.annotate(
        has_published_scores=Exists(published_scores)
    )


@login_required
def exam_score_upload_sample(request):
    if not user_has_feature(request.user, "exam-scores"):
        raise PermissionDenied

    try:
        room = ExamRoom.objects.select_related("exam_period").get(
            pk=request.GET.get("room")
        )
    except (ExamRoom.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "Phòng thi không hợp lệ."}, status=404)
    rows = _score_upload_sample_rows(room, request.GET.get("kind"))
    if rows is None:
        return JsonResponse(
            {"error": "Phòng thi không có thí sinh chưa nhập điểm để tạo file mẫu an toàn."},
            status=400,
        )

    return _csv_download(
        rows,
        f"diem-phong-{room.pk}-{'co-loi' if request.GET.get('kind') == 'invalid' else 'hop-le'}.csv",
    )


def _score_upload_sample_rows(room, kind=None):
    assignments = list(
        ExamRoomAssignment.objects
        .filter(exam_room=room, score__isnull=True)
        .select_related("application")
        .order_by("pk")
    )
    if not assignments:
        return None

    score_scale = room.exam_period.score_scale
    score_rows = [
        ["Thí sinh", "CCCD", "Tên đợt thi", "Phòng thi", "Ca thi", "Điểm số"]
    ]
    for index, assignment in enumerate(assignments):
        score = (
            score_scale + Decimal("1")
            if kind == "invalid" and index == 0
            else (
                score_scale * Decimal(index + 1) / Decimal(len(assignments) + 1)
            ).quantize(Decimal("0.01"))
        )
        score_rows.append([
            assignment.application.full_name,
            assignment.application.id_number,
            room.exam_period.name,
            room.name,
            room.session,
            score,
        ])
    return score_rows


def _score_upload_validate(request, room):
    """Validate uploaded CSV, return JSON preview."""
    if not room:
        return JsonResponse({
            "ok": False,
            "error": "Chưa chọn phòng thi.",
            "valid_count": 0,
            "preview_rows": [],
            "errors": [{"row": 0, "msg": "Chưa chọn phòng thi."}],
        })
    if _room_has_published_scores(room.pk):
        message = "Phòng thi đã có điểm công bố; không thể nhập điểm từ file."
        return JsonResponse({
            "ok": False,
            "valid_count": 0,
            "overwrite_count": 0,
            "preview_rows": [],
            "errors": [{"row": 0, "msg": message}],
            "has_error": True,
            "summary": message,
        })

    file = request.FILES.get("file")
    if not file:
        return JsonResponse({
            "ok": False,
            "error": "Không tìm thấy file.",
            "valid_count": 0,
            "preview_rows": [],
            "errors": [{"row": 0, "msg": "Không tìm thấy file."}],
        })

    entries, errors, preview_rows, rows_preview = _parse_score_upload(file, room)
    overwrite_count = _score_upload_overwrite_count(entries)
    return JsonResponse({
        "ok": not errors,
        "valid_count": len(entries),
        "overwrite_count": overwrite_count,
        "preview_rows": preview_rows,
        "errors": errors,
        "has_error": bool(errors),
        "rows": rows_preview,
        "summary": (
            f"{len(rows_preview)} dòng, {len(entries)} hợp lệ, "
            f"{len(errors)} lỗi."
            + (
                f" {overwrite_count} điểm chưa công bố sẽ bị ghi đè."
                if overwrite_count else ""
            )
        ),
    })


def _score_upload_overwrite_count(entries):
    count = 0
    for entry in entries:
        score = getattr(entry["assignment"], "score", None)
        if score and score.score is not None:
            count += 1
    return count


def _room_has_published_scores(room_id):
    return ExamScore.objects.filter(
        assignment__exam_room_id=room_id,
        is_published=True,
    ).exists()


def _score_upload_confirm(request, room):
    """Save validated CSV data."""
    if not room:
        return JsonResponse({
            "ok": False,
            "error": "Chưa chọn phòng thi.",
            "saved": 0,
            "errors": [{"row": 0, "msg": "Chưa chọn phòng thi."}],
        })
    if _room_has_published_scores(room.pk):
        message = "Phòng thi đã có điểm công bố; không thể nhập điểm từ file."
        return JsonResponse({
            "ok": False,
            "saved": 0,
            "overwrite_count": 0,
            "errors": [{"row": 0, "msg": message}],
            "message": message,
        })

    file = request.FILES.get("file")
    if not file:
        return JsonResponse({
            "ok": False,
            "error": "Không tìm thấy file.",
            "saved": 0,
            "errors": [{"row": 0, "msg": "Không tìm thấy file."}],
        })

    saved = 0
    overwrite_count = 0
    with transaction.atomic():
        file.seek(0)
        entries, errors, _, _ = _parse_score_upload(file, room)
        if errors:
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "overwrite_count": 0,
                "errors": errors,
                "message": "File có lỗi; chưa có điểm nào được lưu. Sửa file rồi tải lên lại.",
            })

        overwrite_count = _score_upload_overwrite_count(entries)
        confirmed_overwrite_count = request.POST.get("confirm_overwrite_count")
        if overwrite_count and confirmed_overwrite_count != str(overwrite_count):
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "overwrite_count": overwrite_count,
                "requires_overwrite_confirmation": True,
                "errors": [],
                "message": (
                    f"File sẽ ghi đè {overwrite_count} điểm chưa công bố. "
                    "Cần xác nhận trước khi tiếp tục."
                ),
            })

        for entry in entries:
            score_obj, _ = ExamScore.objects.get_or_create(
                assignment=entry["assignment"]
            )
            old_snap = snapshot_instance(score_obj)
            score_obj.score = entry["score"]
            score_obj.is_absent = entry["is_absent"]
            score_obj.entered_at = timezone.now()
            score_obj.save()
            saved += 1

            new_snap = snapshot_instance(score_obj)
            record_audit(
                request.user,
                AuditLog.Action.EDIT,
                score_obj,
                object_label=f"Upload điểm {entry['id_number']} — {room.name}",
                old_values=old_snap,
                new_values=new_snap,
            )

    return JsonResponse({
        "ok": saved > 0,
        "saved": saved,
        "overwrite_count": overwrite_count,
        "errors": errors,
        "message": (
            f"Đã nhập {saved} điểm"
            + (
                f", ghi đè {overwrite_count} điểm chưa công bố"
                if overwrite_count else ""
            )
            + f" vào phòng \"{room.name}\"."
        ),
    })


def _parse_score_upload(uploaded, room):
    try:
        content = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        error = {"row": 0, "msg": "File không đúng định dạng UTF-8. Hãy lưu dưới dạng CSV UTF-8."}
        return [], [error], [], []

    reader = csv.DictReader(io.StringIO(content), strict=True)
    headers = [header.strip() if header else "" for header in (reader.fieldnames or [])]
    normalized_headers = [header.casefold() for header in headers]
    if len(set(normalized_headers)) != len(normalized_headers):
        error = {
            "row": 0,
            "msg": "File có tên cột bị lặp; hãy giữ mỗi cột đúng một lần.",
        }
        return [], [error], [], []
    reader.fieldnames = headers

    required_headers = (
        "Thí sinh",
        "CCCD",
        "Tên đợt thi",
        "Phòng thi",
        "Ca thi",
        "Điểm số",
    )
    header_map = {
        header.casefold(): header
        for header in headers
    }
    missing_headers = [
        header
        for header in required_headers
        if header.casefold() not in header_map
    ]
    if missing_headers:
        error = {
            "row": 0,
            "msg": f"File thiếu cột bắt buộc: {', '.join(missing_headers)}.",
        }
        return [], [error], [], []

    try:
        rows = list(reader)
    except csv.Error:
        error = {"row": 0, "msg": "File CSV bị lỗi định dạng."}
        return [], [error], [], []
    if not rows:
        error = {"row": 0, "msg": "File CSV không có dòng dữ liệu."}
        return [], [error], [], []

    lookup = {
        assignment.application.id_number: assignment
        for assignment in room.room_assignments.select_related("application", "score")
    }
    entries = []
    errors = []
    preview_rows = []
    rows_preview = []
    seen_id_numbers = set()
    score_scale = room.exam_period.score_scale
    absent_field = header_map.get("vắng thi") or header_map.get("vang_thi")

    def value_for(row, header):
        return (row.get(header_map[header.casefold()]) or "").strip()

    for row_number, row in enumerate(rows, start=2):
        full_name = value_for(row, "Thí sinh")
        id_number = value_for(row, "CCCD")
        row_period_name = value_for(row, "Tên đợt thi")
        row_room_name = value_for(row, "Phòng thi")
        row_session = value_for(row, "Ca thi")
        raw_score = value_for(row, "Điểm số")
        absent_value = (row.get(absent_field) or "").strip().lower() if absent_field else ""
        is_absent = absent_value in ("1", "true", "x", "có", "co")
        message = None
        score = None
        assignment = lookup.get(id_number)
        existing_score = getattr(assignment, "score", None) if assignment else None
        is_absent = is_absent or bool(existing_score and existing_score.is_absent)

        if None in row:
            message = "Dòng có nhiều giá trị hơn số cột trong tiêu đề."
        elif not full_name:
            message = "Thiếu tên thí sinh."
        elif not id_number:
            message = "Thiếu CCCD."
        elif not row_period_name:
            message = "Thiếu tên đợt thi."
        elif not row_room_name:
            message = "Thiếu phòng thi."
        elif not row_session:
            message = "Thiếu ca thi."
        elif not raw_score and not is_absent:
            message = "Thiếu điểm số."
        elif row_period_name != room.exam_period.name:
            message = (
                "Tên đợt thi trong file không khớp với đợt của phòng đã chọn "
                f"({room.exam_period.name})."
            )
        elif row_room_name and row_room_name != room.name:
            message = f"Phòng thi trong file không khớp với phòng đã chọn ({room.name})."
        elif row_session != room.session:
            message = f"Ca thi trong file không khớp với phòng đã chọn ({room.session})."
        elif not assignment:
            message = f"CCCD {id_number} không có trong phòng thi."
        elif " ".join(full_name.split()).casefold() != " ".join(
            assignment.application.full_name.split()
        ).casefold():
            message = "Tên thí sinh trong file không khớp với CCCD đã xếp trong phòng."
        elif id_number in seen_id_numbers:
            message = f"CCCD {id_number} xuất hiện nhiều lần trong file."
        elif existing_score and existing_score.is_published:
            message = "Điểm đã công bố; không thể ghi đè bằng file CSV."
        elif not is_absent:
            try:
                score = Decimal(raw_score.replace(",", "."))
                if not score.is_finite():
                    message = "Điểm số phải là một số hữu hạn."
                elif max(-score.as_tuple().exponent, 0) > 2:
                    message = "Điểm số không được có quá 2 chữ số thập phân."
                elif len(score.as_tuple().digits) + max(score.as_tuple().exponent, 0) > 5:
                    message = "Điểm số vượt quá độ chính xác cho phép."
                elif score < 0 or score > score_scale:
                    message = f"Điểm {score} ngoài thang điểm 0–{score_scale}."
            except (InvalidOperation, ValueError):
                message = f"Điểm \"{raw_score}\" không hợp lệ."

        if message:
            error = {"row": row_number, "msg": message}
            errors.append(error)
            rows_preview.append({
                "row": row_number,
                "cell": id_number or f"(dòng {row_number})",
                "ok": False,
                "msg": message,
            })
            continue

        seen_id_numbers.add(id_number)
        score_label = "Vắng thi" if is_absent else f"{score} điểm"
        entries.append({
            "assignment": assignment,
            "id_number": id_number,
            "score": score,
            "is_absent": is_absent,
        })
        preview_rows.append({
            "row": row_number,
            "preview_text": (
                f"{full_name} · CCCD {id_number} · {row_period_name} · "
                f"{row_room_name} · {row_session} · {score_label}"
            ),
        })
        rows_preview.append({
            "row": row_number,
            "cell": f"{full_name} — CCCD {id_number}",
            "ok": True,
            "msg": "Vắng thi" if is_absent else "Hợp lệ",
        })

    return entries, errors, preview_rows, rows_preview


# ── Công bố / Thu hồi điểm ───────────────────────────────────────────────────

@login_required
def exam_score_publish(request):
    """
    Công bố hoặc thu hồi công bố điểm thi theo đợt hoặc theo phòng.
    Điều kiện công bố: mọi thí sinh phải có điểm hoặc đánh dấu vắng.
    """
    if not user_has_feature(request.user, "exam-scores"):
        raise PermissionDenied

    periods = ExamPeriod.objects.order_by("-exam_date")
    selected_period_id = request.GET.get("period") or request.POST.get("period")
    selected_room_id = request.GET.get("room") or request.POST.get("room")

    selected_period = None
    selected_room = None
    if selected_period_id:
        selected_period = get_object_or_404(ExamPeriod, pk=selected_period_id)
    if selected_room_id:
        selected_room = get_object_or_404(ExamRoom, pk=selected_room_id)

    rooms_for_period = []
    if selected_period:
        rooms_for_period = ExamRoom.objects.filter(exam_period=selected_period)

    # Tính trạng thái cho từng phòng
    room_status_list = []
    for room in rooms_for_period:
        assignments = room.room_assignments.all()
        total = assignments.count()
        missing = []
        for asgn in assignments:
            try:
                sc = asgn.score
                if sc.score is None and not sc.is_absent:
                    missing.append(asgn.application.full_name)
            except ExamScore.DoesNotExist:
                missing.append(asgn.application.full_name)

        published_count = ExamScore.objects.filter(
            assignment__exam_room=room, is_published=True
        ).count()
        room_status_list.append({
            "room": room,
            "total": total,
            "missing_count": len(missing),
            "missing": missing[:5],
            "published_count": published_count,
            "can_publish": len(missing) == 0 and total > 0,
            "is_all_published": published_count == total and total > 0,
        })

    if request.method == "POST":
        action = request.POST.get("action")  # "publish" or "revoke"
        reason = request.POST.get("reason", "").strip()
        scope_room_id = request.POST.get("scope_room_id")

        if action == "revoke" and not reason:
            messages.error(request, "Vui lòng nhập lý do thu hồi công bố điểm.")
        else:
            scope_rooms = []
            if scope_room_id:
                scope_rooms = [get_object_or_404(ExamRoom, pk=scope_room_id)]
            elif selected_period:
                scope_rooms = list(rooms_for_period)

            published_total = 0
            revoked_total = 0
            block_messages = []

            with transaction.atomic():
                for room in scope_rooms:
                    assignments = room.room_assignments.all()
                    if action == "publish":
                        # Kiểm tra điều kiện
                        missing = []
                        for asgn in assignments:
                            try:
                                sc = asgn.score
                                if sc.score is None and not sc.is_absent:
                                    missing.append(asgn.application.full_name)
                            except ExamScore.DoesNotExist:
                                missing.append(asgn.application.full_name)
                        if missing:
                            block_messages.append(
                                f"Phòng \"{room.name}\": còn {len(missing)} thí sinh chưa có điểm "
                                f"({', '.join(missing[:3])}{'...' if len(missing) > 3 else ''})."
                            )
                            continue
                        # Công bố
                        count = 0
                        for asgn in assignments:
                            sc = asgn.score
                            old = snapshot_instance(sc)
                            sc.is_published = True
                            sc.published_at = timezone.now()
                            sc.save(update_fields=["is_published", "published_at"])
                            record_audit(
                                request.user, AuditLog.Action.EDIT, sc,
                                object_label=f"Công bố điểm {asgn.application.full_name} — {room.name}",
                                old_values=old, new_values=snapshot_instance(sc),
                            )
                            count += 1
                        published_total += count
                    elif action == "revoke":
                        count = 0
                        for asgn in assignments:
                            try:
                                sc = asgn.score
                                old = snapshot_instance(sc)
                                sc.is_published = False
                                sc.published_at = None
                                sc.save(update_fields=["is_published", "published_at"])
                                record_audit(
                                    request.user, AuditLog.Action.EDIT, sc,
                                    object_label=f"Thu hồi điểm {asgn.application.full_name} — {room.name}",
                                    old_values=old, new_values=snapshot_instance(sc),
                                    reason=reason,
                                )
                                count += 1
                            except ExamScore.DoesNotExist:
                                pass
                        revoked_total += count

            for msg in block_messages:
                messages.error(request, msg)
            if published_total:
                messages.success(request, f"Đã công bố điểm cho {published_total} thí sinh.")
            if revoked_total:
                messages.success(request, f"Đã thu hồi công bố điểm cho {revoked_total} thí sinh.")

            return redirect(
                reverse("exam-score-publish")
                + (f"?period={selected_period_id}" if selected_period_id else "")
            )

    page_obj = Paginator(room_status_list, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(request, "training/exam_score_publish.html", {
        "active_menu": "exam-scores",
        "page_title": "Công bố / Thu hồi điểm thi",
        "page_description": "Kiểm tra điều kiện và công bố điểm thi theo đợt hoặc từng phòng.",
        "breadcrumbs": [
            {"label": "Điểm thi", "url": reverse("exam-score-list")},
            {"label": "Công bố điểm"},
        ],
        "periods": periods,
        "selected_period": selected_period,
        "selected_room": selected_room,
        "rooms_for_period": rooms_for_period,
        "room_status_list": page_obj,
        "page_obj": page_obj,
    })

# =============================================================================
# EXAM VIEWS — Pha 4: Xuất MyBK, Thống kê, Báo cáo giáo vụ
# =============================================================================

import csv as _csv
from django.http import HttpResponse

from training.models import (
    ExamRoom, ExamRoomAssignment, ExamScore,
    MyBKExportLog, TrainingClass, Enrollment,
)


# ── Xuất dữ liệu MyBK ────────────────────────────────────────────────────────

@login_required
def mybk_export(request):
    """
    Xuất dữ liệu điểm thi sang CSV để đối chiếu với hệ thống MyBK.
    Chỉ xuất thí sinh đã có điểm đã công bố.
    """
    if not user_has_feature(request.user, "mybk-export"):
        raise PermissionDenied

    periods = ExamPeriod.objects.order_by("-exam_date")
    selected_period_id = request.GET.get("period") or request.POST.get("period")
    selected_period = None
    if selected_period_id:
        selected_period = get_object_or_404(ExamPeriod, pk=selected_period_id)
    elif periods.exists():
        selected_period = periods.first()

    # Số lượng thí sinh đã có điểm công bố trong đợt được chọn
    ready_records_count = 0
    total_assignments_count = 0
    if selected_period:
        total_assignments_count = ExamRoomAssignment.objects.filter(
            exam_room__exam_period=selected_period
        ).count()
        ready_records_count = ExamScore.objects.filter(
            assignment__exam_room__exam_period=selected_period,
            is_published=True
        ).count()

    # Lịch sử xuất MyBK — mặc định hiển thị toàn bộ các lần xuất gần đây nhất
    log_scope = request.GET.get("log_scope", "all")
    if log_scope == "period" and selected_period:
        export_logs = MyBKExportLog.objects.filter(
            exam_period=selected_period
        ).select_related("exported_by", "exam_period").order_by("-exported_at")
    else:
        export_logs = MyBKExportLog.objects.select_related(
            "exported_by", "exam_period"
        ).order_by("-exported_at")
    page_obj = Paginator(export_logs, PAGE_SIZE).get_page(request.GET.get("page"))

    total_export_logs_count = MyBKExportLog.objects.count()
    from django.db.models import Sum as _Sum
    total_exported_records = MyBKExportLog.objects.aggregate(t=_Sum("record_count"))["t"] or 0

    if request.method == "POST" and selected_period:
        # Xuất CSV
        qs = ExamRoomAssignment.objects.filter(
            exam_room__exam_period=selected_period,
        ).select_related("application", "exam_room", "score")

        rows = []
        for asgn in qs:
            try:
                sc = asgn.score
                if not sc.is_published:
                    continue
            except ExamScore.DoesNotExist:
                continue
            rows.append({
                "ho_ten": asgn.application.full_name,
                "so_cccd": asgn.application.id_number,
                "dien_thoai": asgn.application.phone,
                "phong_thi": asgn.exam_room.name,
                "ca_thi": asgn.exam_room.session,
                "diem": f"{sc.score:.2f}".replace(".", ",") if sc.score is not None else "",
                "vang_thi": "1" if sc.is_absent else "0",
                "dat": "1" if (sc.score is not None and sc.score >= selected_period.score_scale * Decimal("0.5")) else "0",
            })

        # Ghi log
        log = MyBKExportLog.objects.create(
            exam_period=selected_period,
            exported_by=request.user,
            record_count=len(rows),
            notes=f"Xuất {len(rows)} bản ghi đã công bố",
        )
        record_audit(
            request.user,
            AuditLog.Action.EDIT,
            log,
            object_label=f"Xuất MyBK đợt {selected_period.name}",
            new_values={"record_count": len(rows)},
        )

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        fname = f"mybk_{selected_period.name.replace(' ', '_')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{fname}"'
        writer = _csv.DictWriter(response, fieldnames=[
            "ho_ten", "so_cccd", "dien_thoai", "phong_thi",
            "ca_thi", "diem", "vang_thi", "dat",
        ])
        writer.writeheader()
        writer.writerows(rows)
        return response

    return render(request, "training/mybk_export.html", {
        "active_menu": "mybk-export",
        "page_title": "Xuất dữ liệu MyBK",
        "page_description": "Xuất file CSV điểm thi đã công bố để đối chiếu với hệ thống quản lý MyBK ĐH Bách Khoa.",
        "breadcrumbs": [{"label": "Xuất dữ liệu MyBK"}],
        "periods": periods,
        "selected_period": selected_period,
        "ready_records_count": ready_records_count,
        "total_assignments_count": total_assignments_count,
        "total_export_logs_count": total_export_logs_count,
        "total_exported_records": total_exported_records,
        "export_logs": page_obj,
        "page_obj": page_obj,
        "log_scope": log_scope,
    })


# ── Thống kê đợt thi ─────────────────────────────────────────────────────────

@login_required
def exam_statistics(request):
    """Trang thống kê tổng quan cho một đợt thi."""
    if not user_has_feature(request.user, "exam-statistics"):
        raise PermissionDenied

    periods = ExamPeriod.objects.order_by("-exam_date")
    selected_period_id = request.GET.get("period", "")
    selected_period = None
    stats = {}
    room_stats = []

    if selected_period_id:
        selected_period = get_object_or_404(ExamPeriod, pk=selected_period_id)
        total_apps = selected_period.applications.count()
        valid_apps = selected_period.applications.filter(
            status=ExamApplication.Status.VALID
        ).count()
        duplicate_apps = selected_period.applications.filter(
            status=ExamApplication.Status.DUPLICATE
        ).count()

        # Điểm thi
        all_scores = ExamScore.objects.filter(
            assignment__exam_room__exam_period=selected_period
        )
        has_score = all_scores.filter(score__isnull=False).count()
        absent_count = all_scores.filter(is_absent=True).count()
        published_count = all_scores.filter(is_published=True).count()

        # Đạt/không đạt
        pass_threshold = selected_period.score_scale * Decimal("0.5")
        passed = all_scores.filter(score__gte=pass_threshold).count()
        failed = all_scores.filter(score__isnull=False, score__lt=pass_threshold).count()

        pass_rate = round(passed * 100 / has_score, 1) if has_score else 0
        fail_rate = round(failed * 100 / has_score, 1) if has_score else 0
        valid_rate = round(valid_apps * 100 / total_apps, 1) if total_apps else 0

        stats = {
            "total_apps": total_apps,
            "valid_apps": valid_apps,
            "duplicate_apps": duplicate_apps,
            "rooms": selected_period.rooms.count(),
            "has_score": has_score,
            "absent_count": absent_count,
            "published_count": published_count,
            "passed": passed,
            "failed": failed,
            "pass_rate": pass_rate,
            "fail_rate": fail_rate,
            "valid_rate": valid_rate,
        }

        # Thống kê theo phòng
        for room in selected_period.rooms.all():
            room_total = room.room_assignments.count()
            room_scores = ExamScore.objects.filter(assignment__exam_room=room)
            room_has_score = room_scores.filter(score__isnull=False).count()
            room_absent = room_scores.filter(is_absent=True).count()
            room_passed = room_scores.filter(score__gte=pass_threshold).count()
            room_failed = max(0, room_has_score - room_passed)
            room_stats.append({
                "room": room,
                "total": room_total,
                "has_score": room_has_score,
                "absent": room_absent,
                "passed": room_passed,
                "failed": room_failed,
                "percent_pass": (
                    round(room_passed * 100 / room_total, 1)
                    if room_total else 0
                ),
            })

    # Thống kê tổng hợp toàn hệ thống
    global_total_periods = ExamPeriod.objects.count()
    global_total_apps = ExamApplication.objects.count()
    global_total_rooms = ExamRoom.objects.count()
    global_total_scores = ExamScore.objects.filter(score__isnull=False).count()
    global_total_absent = ExamScore.objects.filter(is_absent=True).count()

    filter_fields = [
        {
            "name": "period",
            "label": "Đợt thi",
            "placeholder": "Chọn đợt thi cần xem chi tiết...",
            "value": selected_period_id,
            "options": [{"value": str(p.pk), "label": p.name} for p in periods],
        },
    ]

    page_obj = Paginator(room_stats, PAGE_SIZE).get_page(request.GET.get("page"))

    return render(request, "training/exam_statistics.html", {
        "active_menu": "exam-statistics",
        "page_title": "Thống kê đợt thi",
        "page_description": "Tổng quan kết quả đăng ký, điểm thi và tỉ lệ đạt theo từng đợt thi.",
        "breadcrumbs": [{"label": "Thống kê đợt thi"}],
        "filter_fields": filter_fields,
        "periods": periods,
        "selected_period": selected_period,
        "stats": stats,
        "room_stats": page_obj,
        "page_obj": page_obj,
        "global_total_periods": global_total_periods,
        "global_total_apps": global_total_apps,
        "global_total_rooms": global_total_rooms,
        "global_total_scores": global_total_scores,
        "global_total_absent": global_total_absent,
    })


# ── Báo cáo giáo vụ ──────────────────────────────────────────────────────────

@login_required
def report_list(request):
    """Báo cáo tổng hợp giáo vụ: lớp học, đào tạo."""
    if not user_has_feature(request.user, "reports"):
        raise PermissionDenied

    if request.GET.get("export") == "excel":
        return report_export_excel(request)

    from django.db.models import Count
    from training.models import Course

    classes_qs = TrainingClass.objects.select_related("course").annotate(
        enrollment_count=Count("enrollments"),
    ).order_by("-id")

    paginator = Paginator(classes_qs, PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    total_classes_count = TrainingClass.objects.count()
    total_enrolled_learners = Enrollment.objects.count()
    open_classes_count = TrainingClass.objects.filter(status=TrainingClass.Status.OPEN).count()
    total_courses_count = Course.objects.count()

    return render(request, "training/report_list.html", {
        "active_menu": "reports",
        "page_title": "Báo cáo giáo vụ",
        "page_description": "Tổng hợp số liệu lớp học và chương trình đào tạo để báo cáo giáo vụ.",
        "breadcrumbs": [{"label": "Báo cáo giáo vụ"}],
        "class_stats": page_obj,
        "page_obj": page_obj,
        "total_classes_count": total_classes_count,
        "total_enrolled_learners": total_enrolled_learners,
        "open_classes_count": open_classes_count,
        "total_courses_count": total_courses_count,
    })


@login_required
def report_export_excel(request):
    """Xuất file Excel báo cáo tổng hợp giáo vụ (Đào tạo & Lớp học)."""
    if not user_has_feature(request.user, "reports"):
        raise PermissionDenied

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from django.http import HttpResponse
    from django.utils import timezone
    from django.db.models import Count
    from training.models import Course

    wb = openpyxl.Workbook()

    # Sheet 1: Đào tạo - Lớp học
    ws_classes = wb.active
    ws_classes.title = "Đào tạo - Lớp học"

    # Sheet 2: Khóa học - Chương trình
    ws_courses = wb.create_sheet(title="Khóa học - Chương trình")

    # Styling palette
    navy_header_fill = PatternFill(start_color="172B3A", end_color="172B3A", fill_type="solid")
    kpi_bg_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    font_org = Font(name="Calibri", size=10, bold=True, color="005B94")
    font_title = Font(name="Calibri", size=16, bold=True, color="172B3A")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="64748B")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=11, color="0F172A")
    font_kpi_label = Font(name="Calibri", size=9, bold=True, color="475569")
    font_kpi_value = Font(name="Calibri", size=13, bold=True, color="005B94")

    thin_side = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def style_merged_range(ws, cell_range, font=None, fill=None, border=None, alignment=None):
        for row in ws[cell_range]:
            for cell in row:
                if font:
                    cell.font = font
                if fill:
                    cell.fill = fill
                if border:
                    cell.border = border
                if alignment:
                    cell.alignment = alignment

    now_str = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M:%S")
    creator_name = request.user.get_full_name() or request.user.username

    # ─────────────────────────────────────────────────────────────
    # POPULATE SHEET 1: ĐÀO TẠO - LỚP HỌC
    # ─────────────────────────────────────────────────────────────
    ws_classes.views.sheetView[0].showGridLines = True

    # Title block
    ws_classes["A1"] = "TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN — ĐH BÁCH KHOA ĐHQG-HCM"
    ws_classes["A1"].font = font_org

    ws_classes["A2"] = "BÁO CÁO TỔNG HỢP LỚP HỌC ĐÀO TẠO"
    ws_classes["A2"].font = font_title

    ws_classes["A3"] = f"Thời gian xuất: {now_str}   |   Người xuất: {creator_name}"
    ws_classes["A3"].font = font_subtitle

    # Data
    classes = TrainingClass.objects.select_related("course").annotate(
        enrollment_count=Count("enrollments"),
    ).order_by("course__name", "name")

    total_classes = classes.count()
    total_enrollments = sum(c.enrollment_count for c in classes)
    total_capacity = sum(c.max_students for c in classes)
    overall_fill_rate = f"{(total_enrollments / total_capacity * 100):.1f}%" if total_capacity > 0 else "0.0%"

    # KPI boxes
    kpis_c = [
        ("A", "B", "TỔNG SỐ LỚP HỌC", f"{total_classes} lớp"),
        ("C", "E", "TỔNG HỌC VIÊN GHI DANH", f"{total_enrollments} học viên"),
        ("F", "G", "TỶ LỆ LẤP ĐẦY TRUNG BÌNH", overall_fill_rate),
    ]
    for start_c, end_c, lbl, val in kpis_c:
        range_lbl = f"{start_c}5:{end_c}5"
        range_val = f"{start_c}6:{end_c}6"
        ws_classes.merge_cells(range_lbl)
        ws_classes.merge_cells(range_val)
        style_merged_range(ws_classes, range_lbl, font=font_kpi_label, fill=kpi_bg_fill, border=cell_border, alignment=align_center)
        style_merged_range(ws_classes, range_val, font=font_kpi_value, fill=kpi_bg_fill, border=cell_border, alignment=align_center)
        ws_classes[f"{start_c}5"] = lbl
        ws_classes[f"{start_c}6"] = val

    headers_classes = [
        ("STT", 6),
        ("Mã / Tên lớp", 28),
        ("Khóa học / Chương trình", 32),
        ("Hình thức học", 16),
        ("Lịch học", 24),
        ("Ngày khai giảng", 16),
        ("Sĩ số hiện tại", 14),
        ("Sĩ số tối đa", 14),
        ("Tỷ lệ lấp đầy", 16),
        ("Trạng thái", 16),
    ]

    header_row_idx = 8
    ws_classes.row_dimensions[header_row_idx].height = 28
    for col_idx, (col_name, _) in enumerate(headers_classes, start=1):
        cell = ws_classes.cell(row=header_row_idx, column=col_idx, value=col_name)
        cell.font = font_header
        cell.fill = navy_header_fill
        cell.alignment = align_header
        cell.border = cell_border

    row_idx = header_row_idx + 1
    for stt, cls in enumerate(classes, start=1):
        ws_classes.row_dimensions[row_idx].height = 20
        row_fill = zebra_fill if stt % 2 == 0 else white_fill
        fill_pct = (cls.enrollment_count / cls.max_students * 100) if cls.max_students > 0 else 0
        format_display = cls.get_learning_format_display() if hasattr(cls, "get_learning_format_display") else cls.learning_format
        status_display = "Đang mở" if cls.status == TrainingClass.Status.OPEN else "Đã đóng"
        start_date_display = cls.start_date.strftime("%d/%m/%Y") if cls.start_date else "—"

        row_data = [
            (stt, align_center),
            (cls.name, align_left),
            (cls.course.name if cls.course else "—", align_left),
            (format_display, align_center),
            (cls.schedule or "—", align_left),
            (start_date_display, align_center),
            (cls.enrollment_count, align_right),
            (cls.max_students, align_right),
            (f"{fill_pct:.1f}%", align_right),
            (status_display, align_center),
        ]

        for col_idx, (val, alignment) in enumerate(row_data, start=1):
            cell = ws_classes.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_data
            cell.fill = row_fill
            cell.alignment = alignment
            cell.border = cell_border

        row_idx += 1

    for col_idx, (_, default_width) in enumerate(headers_classes, start=1):
        col_letter = get_column_letter(col_idx)
        ws_classes.column_dimensions[col_letter].width = default_width

    # ─────────────────────────────────────────────────────────────
    # POPULATE SHEET 2: KHÓA HỌC - CHƯƠNG TRÌNH
    # ─────────────────────────────────────────────────────────────
    ws_courses.views.sheetView[0].showGridLines = True

    ws_courses["A1"] = "TRUNG TÂM KỸ THUẬT ĐIỆN TOÁN — ĐH BÁCH KHOA ĐHQG-HCM"
    ws_courses["A1"].font = font_org

    ws_courses["A2"] = "BÁO CÁO TỔNG HỢP CHƯƠNG TRÌNH ĐÀO TẠO"
    ws_courses["A2"].font = font_title

    ws_courses["A3"] = f"Thời gian xuất: {now_str}   |   Người xuất: {creator_name}"
    ws_courses["A3"].font = font_subtitle

    courses = Course.objects.annotate(
        class_count=Count("classes"),
        enrollment_count=Count("classes__enrollments"),
    ).order_by("name")

    headers_courses = [
        ("STT", 6),
        ("Tên khóa học / Chương trình", 36),
        ("Ngưỡng đạt chuẩn", 18),
        ("Tổng số lớp học", 18),
        ("Học viên ghi danh", 20),
    ]

    header_row_idx = 5
    ws_courses.row_dimensions[header_row_idx].height = 28
    for col_idx, (col_name, _) in enumerate(headers_courses, start=1):
        cell = ws_courses.cell(row=header_row_idx, column=col_idx, value=col_name)
        cell.font = font_header
        cell.fill = navy_header_fill
        cell.alignment = align_header
        cell.border = cell_border

    row_idx = header_row_idx + 1
    for stt, c in enumerate(courses, start=1):
        ws_courses.row_dimensions[row_idx].height = 20
        row_fill = zebra_fill if stt % 2 == 0 else white_fill

        row_data = [
            (stt, align_center),
            (c.name, align_left),
            (f"{c.pass_threshold:.0f}%" if c.pass_threshold else "—", align_center),
            (c.class_count, align_right),
            (c.enrollment_count, align_right),
        ]

        for col_idx, (val, alignment) in enumerate(row_data, start=1):
            cell = ws_courses.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font_data
            cell.fill = row_fill
            cell.alignment = alignment
            cell.border = cell_border

        row_idx += 1

    for col_idx, (_, default_width) in enumerate(headers_courses, start=1):
        col_letter = get_column_letter(col_idx)
        ws_courses.column_dimensions[col_letter].width = default_width

    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
    filename = f"Bao_cao_giao_vu_CCE_{timestamp}.xlsx"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response
