import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from training.access import user_has_feature
from training.audit import record_audit, snapshot_instance
from training.models import (
    AuditLog,
    ClassAssignment,
    Enrollment,
    FinalCourseResult,
    Learner,
    TrainingClass,
)
from training.navigation import roles_for_user
from training.pagination import PAGE_SIZE


MAX_SCORE_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_SCORE_UPLOAD_ROWS = 5000
RESULT_ROLES = {"Giáo vụ", "Quản trị viên"}
SCORE_FILE_FIELDS = {
    "class_name": {"lop", "ten_lop", "class", "class_name"},
    "full_name": {"ho_va_ten", "full_name", "name"},
    "id_number": {"so_cccd", "cccd", "id_number"},
    "score": {"diem_cuoi_khoa", "diem", "score", "final_score"},
}
SCORE_FIELD_LABELS = {
    "class_name": "Lớp",
    "full_name": "Họ và tên",
    "id_number": "Số CCCD",
    "score": "Điểm cuối khóa",
}


def _can_manage_final_results(user):
    roles = set(roles_for_user(user))
    return user.is_superuser or (
        bool(roles & RESULT_ROLES)
        and user_has_feature(user, "classes")
    )


def _can_access_final_results(user, training_class):
    if _can_manage_final_results(user):
        return True
    return (
        "Giáo viên" in roles_for_user(user)
        and user_has_feature(user, "my-classes")
        and ClassAssignment.objects.filter(
            training_class=training_class,
            teacher__user=user,
            is_active=True,
        ).exists()
    )


def _class_learners(training_class):
    return (
        Learner.objects.filter(
            Q(enrollments__training_class=training_class)
            | Q(final_course_results__training_class=training_class)
        )
        .distinct()
        .order_by("full_name", "pk")
    )


def _parse_score(raw_value):
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return None, "Vui lòng nhập điểm cuối khóa."

    try:
        score = Decimal(raw_value.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None, "Điểm cuối khóa không phải là số hợp lệ."

    if not score.is_finite():
        return None, "Điểm cuối khóa phải là một số hữu hạn."
    if max(-score.as_tuple().exponent, 0) > 2:
        return None, "Điểm cuối khóa không được có quá 2 chữ số thập phân."
    if len(score.as_tuple().digits) + max(score.as_tuple().exponent, 0) > 5:
        return None, "Điểm cuối khóa vượt quá độ chính xác cho phép."
    if score < 0 or score > 100:
        return None, "Điểm cuối khóa phải nằm trong khoảng 0 đến 100."
    return score, None


def _outcome(score, threshold):
    if score is None:
        return "Chưa có điểm"
    return "Đạt" if score >= threshold else "Không đạt"


def _render_results(
    request,
    training_class,
    *,
    score_values=None,
    field_errors=None,
    reason_error="",
    reason_value="",
    status=200,
):
    can_manage_results = _can_manage_final_results(request.user)
    learners = _class_learners(training_class)
    page_obj = Paginator(learners, PAGE_SIZE).get_page(request.GET.get("page"))
    learner_ids = [learner.pk for learner in page_obj.object_list]
    saved_results = {
        result.learner_id: result
        for result in FinalCourseResult.objects.filter(
            training_class=training_class,
            learner_id__in=learner_ids,
        )
    }

    rows = []
    for learner in page_obj.object_list:
        result = saved_results.get(learner.pk)
        if score_values is not None and learner.pk in score_values:
            score_value = score_values[learner.pk]
        else:
            score_value = str(result.score) if result else ""

        score, error = (
            _parse_score(score_value)
            if str(score_value).strip()
            else (None, None)
        )
        rows.append({
            "learner": learner,
            "score_value": score_value,
            "outcome": _outcome(score, training_class.course.pass_threshold),
            "error": (field_errors or {}).get(learner.pk) or error,
            "input_id": f"score-{learner.pk}",
        })

    enrolled_ids = list(
        Enrollment.objects.filter(training_class=training_class)
        .values_list("learner_id", flat=True)
    )
    scored_ids = set(
        FinalCourseResult.objects.filter(
            training_class=training_class,
            learner_id__in=enrolled_ids,
            score__isnull=False,
        ).values_list("learner_id", flat=True)
    )
    missing_score_count = len(set(enrolled_ids) - scored_ids)
    has_sample_rows = _class_learners(training_class).exclude(
        id_number__isnull=True
    ).exclude(id_number="").exists()

    class_breadcrumb = (
        {"label": "Lớp học", "url": reverse("admin:training_trainingclass_changelist")}
        if can_manage_results
        else {"label": "Lớp của tôi", "url": reverse("my-class-list")}
    )
    context = {
        "active_menu": "classes" if can_manage_results else "my-classes",
        "page_title": f"Kết quả cuối khóa — {training_class.name}",
        "page_description": (
            f"Ngưỡng đạt: {training_class.course.pass_threshold} điểm. "
            "Bài đánh giá diễn ra ngoài hệ thống."
        ),
        "breadcrumbs": [
            class_breadcrumb,
            {
                "label": training_class.name,
                "url": reverse("class-detail", args=(training_class.pk,)),
            },
            {"label": "Kết quả cuối khóa"},
        ],
        "training_class": training_class,
        "can_manage_results": can_manage_results,
        "can_edit_scores": can_manage_results or not training_class.final_results_locked,
        "can_upload_results": can_manage_results or not training_class.final_results_locked,
        "rows": rows,
        "page_obj": page_obj,
        "missing_score_count": missing_score_count,
        "final_results_locked": training_class.final_results_locked,
        "final_results_locked_at": training_class.final_results_locked_at,
        "final_results_locked_by": training_class.final_results_locked_by,
        "reason_error": reason_error,
        "reason_value": reason_value,
        "results_url": reverse("class-results", args=(training_class.pk,)),
        "upload_url": reverse("class-results-upload", args=(training_class.pk,)),
        "upload_sample_url": reverse(
            "class-results-upload-sample",
            args=(training_class.pk,),
        ),
        "has_sample_rows": has_sample_rows,
        "can_finalize": (
            can_manage_results
            and bool(enrolled_ids)
            and missing_score_count == 0
            and not training_class.final_results_locked
        ),
    }
    return render(request, "training/class_final_results.html", context, status=status)


def _redirect_to_results(request, training_class):
    url = reverse("class-results", args=(training_class.pk,))
    page = request.GET.get("page")
    if page:
        url = f"{url}?page={page}"
    return redirect(url)


def _finalize_results(request, training_class):
    with transaction.atomic():
        training_class = get_object_or_404(
            TrainingClass.objects.select_for_update().select_related("course"),
            pk=training_class.pk,
        )
        if training_class.final_results_locked:
            messages.info(request, "Kết quả lớp này đã được chốt trước đó.")
            return _redirect_to_results(request, training_class)

        enrolled_ids = list(
            Enrollment.objects.filter(training_class=training_class)
            .values_list("learner_id", flat=True)
        )
        if not enrolled_ids:
            messages.error(request, "Lớp chưa có học viên để chốt kết quả.")
            return _redirect_to_results(request, training_class)

        scored_ids = set(
            FinalCourseResult.objects.filter(
                training_class=training_class,
                learner_id__in=enrolled_ids,
                score__isnull=False,
            ).values_list("learner_id", flat=True)
        )
        missing_count = len(set(enrolled_ids) - scored_ids)
        if missing_count:
            messages.error(
                request,
                f"Còn {missing_count} học viên chưa có điểm; chưa thể chốt kết quả.",
            )
            return _redirect_to_results(request, training_class)

        old_values = snapshot_instance(training_class)
        training_class.final_results_locked_at = timezone.now()
        training_class.final_results_locked_by = request.user
        training_class.save(
            update_fields=("final_results_locked_at", "final_results_locked_by")
        )
        record_audit(
            request.user,
            AuditLog.Action.EDIT,
            training_class,
            old_values=old_values,
            new_values=snapshot_instance(training_class),
            reason="Chốt kết quả cuối khóa.",
        )

    messages.success(request, "Kết quả cuối khóa đã được chốt.")
    return _redirect_to_results(request, training_class)


def _validate_manual_scores(request, page_obj):
    score_values = {}
    entries = []
    errors = {}
    for learner in page_obj.object_list:
        key = f"score_{learner.pk}"
        if key not in request.POST:
            continue

        raw_value = request.POST.get(key, "")
        score_values[learner.pk] = raw_value
        if not raw_value.strip():
            continue

        score, error = _parse_score(raw_value)
        if error:
            errors[learner.pk] = error
        else:
            entries.append({"learner": learner, "score": score})
    return entries, score_values, errors


def _save_result_entries(actor, training_class, entries, reason=""):
    saved_count = 0
    for entry in entries:
        learner = entry["learner"]
        score = entry["score"]
        result = (
            FinalCourseResult.objects.select_for_update()
            .filter(training_class=training_class, learner=learner)
            .first()
        )
        if result and result.score == score:
            continue

        old_values = snapshot_instance(result) if result else {}
        if result is None:
            result = FinalCourseResult(
                training_class=training_class,
                learner=learner,
                score=score,
            )
            action = AuditLog.Action.CREATE
        else:
            result.score = score
            action = AuditLog.Action.EDIT
        result.modified_by = actor
        result.full_clean()
        result.save()
        record_audit(
            actor,
            action,
            result,
            old_values=old_values,
            new_values=snapshot_instance(result),
            reason=reason,
            object_label=f"{learner.full_name} — {training_class.name}",
        )
        saved_count += 1
    return saved_count


@login_required
@require_http_methods(["GET", "POST"])
def class_final_results(request, class_id):
    training_class = get_object_or_404(
        TrainingClass.objects.select_related("course", "final_results_locked_by"),
        pk=class_id,
    )
    if not _can_access_final_results(request.user, training_class):
        raise PermissionDenied
    can_manage_results = _can_manage_final_results(request.user)
    if request.method == "GET":
        return _render_results(request, training_class)

    action = request.POST.get("action")
    if action == "finalize":
        if not can_manage_results:
            raise PermissionDenied
        return _finalize_results(request, training_class)
    if not can_manage_results and training_class.final_results_locked:
        raise PermissionDenied
    if action == "save_locked" and not can_manage_results:
        raise PermissionDenied
    if action not in {"save", "save_locked"}:
        raise PermissionDenied

    page_obj = Paginator(_class_learners(training_class), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    entries, score_values, field_errors = _validate_manual_scores(request, page_obj)
    reason = request.POST.get("reason", "").strip()
    reason_error = ""
    saved_count = 0

    with transaction.atomic():
        locked_class = get_object_or_404(
            TrainingClass.objects.select_for_update().select_related("course"),
            pk=training_class.pk,
        )
        if not can_manage_results and locked_class.final_results_locked:
            raise PermissionDenied
        if locked_class.final_results_locked and action != "save_locked":
            reason_error = "Kết quả đã được chốt. Hãy dùng thao tác sửa sau chốt."
        elif not locked_class.final_results_locked and action == "save_locked":
            reason_error = "Kết quả chưa được chốt; hãy lưu bằng thao tác thông thường."
        elif locked_class.final_results_locked and not reason:
            reason_error = "Vui lòng nhập lý do sửa điểm sau khi chốt."

        allowed_ids = set(
            _class_learners(locked_class)
            .filter(pk__in=[entry["learner"].pk for entry in entries])
            .values_list("pk", flat=True)
        )
        if len(allowed_ids) != len(entries):
            reason_error = "Danh sách học viên đã thay đổi. Tải lại trang rồi thử lại."

        if not field_errors and not reason_error:
            saved_count = _save_result_entries(
                request.user,
                locked_class,
                entries,
                reason=reason if locked_class.final_results_locked else "",
            )

    if field_errors or reason_error:
        return _render_results(
            request,
            training_class,
            score_values=score_values,
            field_errors=field_errors,
            reason_error=reason_error,
            reason_value=reason,
        )

    messages.success(
        request,
        f"Đã lưu điểm cho {saved_count} học viên."
        if saved_count
        else "Không có thay đổi điểm cần lưu.",
    )
    return _redirect_to_results(request, training_class)


def _normalize_header(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    ).replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _upload_error(row, column, message):
    return {
        "row": row,
        "column": column,
        "msg": message,
        "message": message,
    }


def _parse_final_result_csv(uploaded, training_class):
    if uploaded.size > MAX_SCORE_UPLOAD_SIZE:
        return [], [
            _upload_error(1, "Tệp", "File vượt quá giới hạn 5 MB.")
        ], []
    try:
        content = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [
            _upload_error(
                1,
                "Tệp",
                "File không đúng định dạng UTF-8. Hãy lưu dưới dạng CSV UTF-8.",
            )
        ], []

    reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
    try:
        raw_headers = reader.fieldnames or []
    except csv.Error:
        return [], [
            _upload_error(1, "Tiêu đề", "Dòng tiêu đề của file CSV bị lỗi định dạng.")
        ], []
    headers = [header.strip() if header else "" for header in raw_headers]
    normalized_headers = [_normalize_header(header) for header in headers]
    if not headers:
        return [], [
            _upload_error(1, "Tiêu đề", "File không có dòng tiêu đề.")
        ], []
    if len(set(normalized_headers)) != len(normalized_headers):
        return [], [
            _upload_error(
                1,
                "Tiêu đề",
                "File có tên cột bị lặp; hãy giữ mỗi cột đúng một lần.",
            )
        ], []

    header_map = {}
    for index, normalized in enumerate(normalized_headers):
        for field_name, aliases in SCORE_FILE_FIELDS.items():
            if normalized in aliases:
                if field_name in header_map:
                    return [], [
                        _upload_error(
                            1,
                            "Tiêu đề",
                            f"File có nhiều cột cùng biểu thị “{SCORE_FIELD_LABELS[field_name]}”.",
                        )
                    ], []
                header_map[field_name] = headers[index]

    missing = [
        SCORE_FIELD_LABELS[field_name]
        for field_name in SCORE_FILE_FIELDS
        if field_name not in header_map
    ]
    if missing:
        return [], [
            _upload_error(
                1,
                "Tiêu đề",
                "File thiếu cột bắt buộc: " + ", ".join(missing) + ".",
            )
        ], []

    try:
        raw_rows = list(reader)
    except csv.Error:
        return [], [
            _upload_error(1, "Tệp", "File CSV bị lỗi định dạng.")
        ], []
    if len(raw_rows) > MAX_SCORE_UPLOAD_ROWS:
        return [], [
            _upload_error(
                MAX_SCORE_UPLOAD_ROWS + 2,
                "Tệp",
                f"File vượt quá giới hạn {MAX_SCORE_UPLOAD_ROWS} dòng dữ liệu.",
            )
        ], []

    learners_by_id = {
        learner.id_number: learner
        for learner in _class_learners(training_class)
        .exclude(id_number__isnull=True)
        .exclude(id_number="")
    }
    entries = []
    errors = []
    preview_rows = []
    seen_id_numbers = set()

    for row_number, row in enumerate(raw_rows, start=2):
        if not any(str(value or "").strip() for value in row.values()):
            continue

        if None in row:
            errors.append(
                _upload_error(
                    row_number,
                    "Dòng dữ liệu",
                    "Dòng có nhiều giá trị hơn số cột trong tiêu đề.",
                )
            )
            continue

        full_name = (row.get(header_map["full_name"]) or "").strip()
        id_number = (row.get(header_map["id_number"]) or "").strip()
        raw_score = (row.get(header_map["score"]) or "").strip()
        class_name = (row.get(header_map["class_name"]) or "").strip()
        learner = learners_by_id.get(id_number)
        error = None
        error_column = ""
        if not class_name:
            error_column, error = "Lớp", "Vui lòng nhập lớp."
        elif " ".join(class_name.split()).casefold() != " ".join(
            training_class.name.split()
        ).casefold():
            error_column, error = (
                "Lớp",
                "Lớp trong file không khớp với lớp đang mở.",
            )
        elif not full_name:
            error_column, error = "Họ và tên", "Vui lòng nhập họ và tên."
        elif not id_number:
            error_column, error = "Số CCCD", "Vui lòng nhập số CCCD."
        elif not raw_score:
            error_column, error = "Điểm cuối khóa", "Vui lòng nhập điểm cuối khóa."
        elif id_number in seen_id_numbers:
            error_column, error = "Số CCCD", "Số CCCD xuất hiện nhiều lần trong file."
        elif learner is None:
            error_column, error = (
                "Số CCCD",
                "Học viên không thuộc danh sách kết quả của lớp đã chọn.",
            )
        elif " ".join(full_name.split()).casefold() != " ".join(
            learner.full_name.split()
        ).casefold():
            error_column, error = (
                "Họ và tên",
                "Họ và tên trong file không khớp với số CCCD.",
            )

        score = None
        if error is None:
            score, error = _parse_score(raw_score)
            if error:
                error_column = "Điểm cuối khóa"

        if error:
            errors.append(_upload_error(row_number, error_column, error))
            continue

        seen_id_numbers.add(id_number)
        entries.append({"learner": learner, "score": score})
        preview_rows.append({
            "row": row_number,
            "preview_text": (
                f"{training_class.name} — {full_name} — {id_number} — {score} điểm"
            ),
            "status": "valid",
        })

    if not entries and not errors:
        errors.append(
            _upload_error(1, "Dữ liệu", "File không có dòng điểm để nhập.")
        )
    return entries, errors, preview_rows


def _json_upload_failure(message, errors=None, valid_count=0, preview_rows=None):
    return JsonResponse({
        "ok": False,
        "saved": 0,
        "valid_count": valid_count,
        "errors": errors or [],
        "preview_rows": preview_rows or [],
        "message": message,
    })


@login_required
@require_http_methods(["GET", "POST"])
def class_final_result_upload(request, class_id):
    training_class = get_object_or_404(
        TrainingClass.objects.select_related("course"),
        pk=class_id,
    )
    if not _can_access_final_results(request.user, training_class):
        raise PermissionDenied
    can_manage_results = _can_manage_final_results(request.user)
    if not can_manage_results and training_class.final_results_locked:
        raise PermissionDenied

    if request.method == "POST":
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return _json_upload_failure(
                "Vui lòng chọn file cần nhập.",
                [_upload_error(1, "Tệp", "Vui lòng chọn file cần nhập.")],
            )
        if not uploaded.name.lower().endswith(".csv"):
            return _json_upload_failure(
                "Chỉ hỗ trợ file CSV UTF-8.",
                [_upload_error(1, "Tệp", "Chỉ hỗ trợ file CSV UTF-8.")],
            )

        if request.POST.get("preview") == "1":
            entries, errors, preview_rows = _parse_final_result_csv(
                uploaded,
                training_class,
            )
            return JsonResponse({
                "ok": not errors,
                "saved": 0,
                "valid_count": len(entries),
                "errors": errors,
                "preview_rows": preview_rows,
                "message": (
                    "File có lỗi; chưa lưu dữ liệu nào."
                    if errors
                    else "Chưa lưu dữ liệu. Kiểm tra nội dung rồi xác nhận để lưu."
                ),
            })

        reason = request.POST.get("reason", "").strip()
        with transaction.atomic():
            locked_class = get_object_or_404(
                TrainingClass.objects.select_for_update().select_related("course"),
                pk=training_class.pk,
            )
            if not can_manage_results and locked_class.final_results_locked:
                raise PermissionDenied
            if locked_class.final_results_locked and not reason:
                return _json_upload_failure(
                    "Vui lòng nhập lý do sửa điểm sau khi chốt."
                )

            entries, errors, preview_rows = _parse_final_result_csv(
                uploaded,
                locked_class,
            )
            if errors:
                return _json_upload_failure(
                    "File có lỗi; chưa lưu dữ liệu nào.",
                    errors,
                    valid_count=len(entries),
                    preview_rows=preview_rows,
                )

            saved_count = _save_result_entries(
                request.user,
                locked_class,
                entries,
                reason=reason if locked_class.final_results_locked else "",
            )

        return JsonResponse({
            "ok": True,
            "saved": saved_count,
            "valid_count": saved_count,
            "errors": [],
            "preview_rows": preview_rows,
            "message": f"Đã lưu điểm cuối khóa cho {saved_count} học viên.",
        })

    learners = _class_learners(training_class)
    has_learners = learners.exists()
    has_sample_rows = learners.exclude(id_number__isnull=True).exclude(id_number="").exists()
    return render(
        request,
        "training/class_final_results_upload.html",
        {
            "active_menu": "classes" if can_manage_results else "my-classes",
            "page_title": f"Upload file điểm — {training_class.name}",
            "page_description": (
                "Chọn file CSV UTF-8 có lớp, họ tên, CCCD và điểm cuối khóa. "
                "Nếu có bất kỳ lỗi nào, chưa có dữ liệu nào được lưu."
            ),
            "breadcrumbs": [
                {
                    "label": training_class.name,
                    "url": reverse("class-detail", args=(training_class.pk,)),
                },
                {
                    "label": "Kết quả cuối khóa",
                    "url": reverse("class-results", args=(training_class.pk,)),
                },
                {"label": "Upload file điểm"},
            ],
            "training_class": training_class,
            "has_learners": has_learners,
            "has_sample_rows": has_sample_rows,
            "locked": training_class.final_results_locked,
            "upload_url": reverse(
                "class-results-upload",
                args=(training_class.pk,),
            ),
            "sample_url": reverse(
                "class-results-upload-sample",
                args=(training_class.pk,),
            ),
            "results_url": reverse(
                "class-results",
                args=(training_class.pk,),
            ),
        },
    )


@login_required
@require_http_methods(["GET"])
def class_final_result_upload_sample(request, class_id):
    training_class = get_object_or_404(TrainingClass, pk=class_id)
    if not _can_access_final_results(request.user, training_class):
        raise PermissionDenied
    learners = (
        _class_learners(training_class)
        .exclude(id_number__isnull=True)
        .exclude(id_number="")
    )
    results = {
        result.learner_id: result
        for result in FinalCourseResult.objects.filter(
            training_class=training_class,
            learner__in=learners,
        )
    }

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Lớp", "Họ và tên", "Số CCCD", "Điểm cuối khóa"])
    for learner in learners:
        result = results.get(learner.pk)
        writer.writerow([
            training_class.name,
            learner.full_name,
            learner.id_number,
            str(result.score) if result else "",
        ])

    response = HttpResponse(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="ket-qua-cuoi-khoa-lop-{training_class.pk}.csv"'
    )
    return response
