import csv
import io
import re
import unicodedata
import zipfile
from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from training.access import user_has_feature
from training.audit import record_audit, snapshot_instance
from training.forms import LearnerImportRowForm
from training.models import AuditLog, Learner


MAX_IMPORT_SIZE = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
REQUIRED_FIELDS = ("full_name", "date_of_birth", "id_number", "phone", "email")
HEADER_ALIASES = {
    "full_name": {"full_name", "ho_va_ten", "ten_hoc_vien"},
    "date_of_birth": {"date_of_birth", "ngay_sinh"},
    "id_number": {"id_number", "so_cccd", "cccd"},
    "phone": {"phone", "so_dien_thoai", "dien_thoai"},
    "email": {"email", "dia_chi_email"},
}


def _normalize_header(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = normalized.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _read_rows(uploaded):
    extension = uploaded.name.rsplit(".", 1)[-1].casefold() if "." in uploaded.name else ""
    if extension not in {"csv", "xlsx"}:
        return [], [{"row": 1, "message": "Chỉ hỗ trợ file CSV hoặc XLSX."}]
    if uploaded.size > MAX_IMPORT_SIZE:
        return [], [{"row": 1, "message": "File vượt quá giới hạn 5 MB."}]

    content = uploaded.read()
    workbook = None
    parse_errors = (
        UnicodeDecodeError,
        csv.Error,
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
    )
    if extension == "xlsx":
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException

        parse_errors += (InvalidFileException,)

    try:
        if extension == "csv":
            text = content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            table_rows = iter(reader)
        else:
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            table_rows = iter(workbook.active.iter_rows(values_only=True))
        header_row = next(table_rows, None)
        if header_row is None:
            return [], [{"row": 1, "message": "File không có tiêu đề hoặc dữ liệu."}]

        return _parse_table_rows(header_row, table_rows)
    except parse_errors as error:
        return [], [{"row": 1, "message": f"Không đọc được file: {error}"}]
    finally:
        if workbook:
            workbook.close()


def _parse_table_rows(header_row, table_rows):
    if header_row is None:
        return [], [{"row": 1, "message": "File không có tiêu đề hoặc dữ liệu."}]

    headers = {}
    for index, value in enumerate(header_row):
        normalized = _normalize_header(value)
        for field_name, aliases in HEADER_ALIASES.items():
            if normalized in aliases and field_name not in headers:
                headers[field_name] = index

    missing = [field for field in REQUIRED_FIELDS if field not in headers]
    if missing:
        labels = {
            "full_name": "Họ và tên",
            "date_of_birth": "Ngày sinh",
            "id_number": "Số CCCD",
            "phone": "Số điện thoại",
            "email": "Email",
        }
        return [], [{
            "row": 1,
            "message": "Thiếu cột bắt buộc: " + ", ".join(labels[field] for field in missing) + ".",
        }]

    rows = []
    for row_number, values in enumerate(table_rows, start=2):
        if row_number > MAX_IMPORT_ROWS + 1:
            return [], [{
                "row": row_number,
                "message": f"File vượt quá giới hạn {MAX_IMPORT_ROWS} dòng dữ liệu.",
            }]
        if not any(str(value or "").strip() for value in values):
            continue
        rows.append({
            "row": row_number,
            "values": {
                field: _cell_text(values[index] if index < len(values) else "")
                for field, index in headers.items()
            },
        })
    return rows, []


def _validate_rows(rows):
    errors = []
    valid_rows = []
    for row in rows:
        form = LearnerImportRowForm(row["values"])
        if form.is_valid():
            valid_rows.append({
                "row": row["row"],
                "data": form.cleaned_data,
            })
        else:
            for field_errors in form.errors.values():
                errors.extend(
                    {"row": row["row"], "message": str(message)}
                    for message in field_errors
                )

    existing = {
        learner.id_number: learner
        for learner in Learner.objects.filter(
            id_number__in={item["data"]["id_number"] for item in valid_rows}
        )
    }
    preview_rows = []
    importable_rows = []
    seen_numbers = set()
    duplicate_count = 0
    for item in valid_rows:
        id_number = item["data"]["id_number"]
        duplicate = existing.get(id_number)
        if duplicate or id_number in seen_numbers:
            duplicate_count += 1
            preview = {
                "row": item["row"],
                "full_name": item["data"]["full_name"],
                "id_number": id_number,
                "status": "duplicate",
            }
            if duplicate:
                preview["duplicate_url"] = reverse(
                    "admin:training_learner_change",
                    args=(duplicate.pk,),
                )
            preview_rows.append(preview)
            continue

        seen_numbers.add(id_number)
        importable_rows.append(item)
        preview_rows.append({
            "row": item["row"],
            "full_name": item["data"]["full_name"],
            "id_number": id_number,
            "status": "valid",
        })

    return valid_rows, importable_rows, preview_rows, errors, duplicate_count


def _response_payload(rows, for_preview, actor):
    valid_rows, importable_rows, preview_rows, errors, duplicate_count = _validate_rows(rows)
    if for_preview:
        return {
            "ok": True,
            "saved": 0,
            "errors": errors,
            "valid_count": len(valid_rows),
            "duplicate_count": duplicate_count,
            "preview_rows": preview_rows,
        }

    saved = 0
    for item in importable_rows:
        data = item["data"]
        with transaction.atomic():
            try:
                with transaction.atomic():
                    learner = Learner.objects.create(**data)
            except IntegrityError:
                duplicate_count += 1
                existing = Learner.objects.filter(id_number=data["id_number"]).first()
                preview_rows.append({
                    "row": item["row"],
                    "full_name": data["full_name"],
                    "id_number": data["id_number"],
                    "status": "duplicate",
                    **(
                        {
                            "duplicate_url": reverse(
                                "admin:training_learner_change",
                                args=(existing.pk,),
                            )
                        }
                        if existing
                        else {}
                    ),
                })
                continue
            record_audit(
                actor,
                AuditLog.Action.CREATE,
                learner,
                new_values=snapshot_instance(learner),
                reason=f"Nhập từ file, dòng {item['row']}.",
            )
        saved += 1

    return {
        "ok": True,
        "saved": saved,
        "errors": errors,
        "valid_count": len(valid_rows),
        "duplicate_count": duplicate_count,
        "preview_rows": preview_rows,
    }


@login_required
@require_http_methods(["GET", "POST"])
def learner_import(request):
    if not user_has_feature(request.user, "learners"):
        raise PermissionDenied

    if request.method == "POST":
        uploaded = request.FILES.get("file")
        if uploaded is None:
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "errors": [{"row": 1, "message": "Vui lòng chọn file cần nhập."}],
                "valid_count": 0,
                "duplicate_count": 0,
                "preview_rows": [],
            })
        rows, errors = _read_rows(uploaded)
        if errors:
            return JsonResponse({
                "ok": False,
                "saved": 0,
                "errors": errors,
                "valid_count": 0,
                "duplicate_count": 0,
                "preview_rows": [],
            })
        return JsonResponse(
            _response_payload(rows, request.POST.get("preview") == "1", request.user)
        )

    return render(request, "training/learner_import.html", {
        "active_menu": "learners",
        "page_title": "Nhập học viên từ file",
        "page_description": "Kiểm tra và nhập hồ sơ học viên từ CSV hoặc Excel.",
        "breadcrumbs": [
            {"label": "Học viên", "url": reverse("admin:training_learner_changelist")},
            {"label": "Nhập từ file"},
        ],
        "import_url": reverse("learner-import"),
    })
