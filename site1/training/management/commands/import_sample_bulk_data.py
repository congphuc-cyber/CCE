from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from openpyxl import load_workbook

from training.models import (
    ClassAssignment,
    Course,
    Enrollment,
    ExamApplication,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
    FinalCourseResult,
    Learner,
    Teacher,
    TrainingClass,
)


DEFAULT_SOURCE = settings.BASE_DIR.parent / "sample_bulk_data"
REQUIRED_WORKBOOKS = (
    "01_Hoc_vien_1000.xlsx",
    "02_Ho_so_thi_sinh_1000.xlsx",
    "03_Dot_thi_5.xlsx",
    "04_Phong_thi_va_xep_phong.xlsx",
    "05_Lop_hoc_va_ghi_danh.xlsx",
    "06_Giao_vien_va_phan_cong.xlsx",
    "07_Diem_cuoi_khoa_1000.xlsx",
    "08_Diem_thi_1000.xlsx",
)


def _text(value, label):
    if value is None:
        raise CommandError(f"Thiếu giá trị ở cột {label}.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _date(value, label):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_text(value, label))
    except ValueError as exc:
        raise CommandError(f"Ngày không hợp lệ ở cột {label}: {value!r}.") from exc


def _decimal(value, label):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise CommandError(f"Giá trị số không hợp lệ ở cột {label}: {value!r}.") from exc


def _read_sheet(root, filename, sheet_name):
    path = root / filename
    if not path.is_file():
        raise CommandError(f"Không tìm thấy file dữ liệu: {path}")

    workbook = None
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        if sheet_name not in workbook.sheetnames:
            raise CommandError(f"File {filename} không có sheet {sheet_name!r}.")

        rows = workbook[sheet_name].iter_rows(values_only=True)
        header_row = next(rows, None)
        if not header_row:
            raise CommandError(f"Sheet {sheet_name!r} trong {filename} đang trống.")
        headers = [str(value).strip() if value is not None else "" for value in header_row]
        result = []
        for row_number, values in enumerate(rows, start=2):
            if not any(value is not None and str(value).strip() for value in values):
                continue
            result.append({
                "row": row_number,
                "values": dict(zip(headers, values)),
            })
        return result
    except (OSError, ValueError, KeyError) as exc:
        raise CommandError(f"Không đọc được {path}: {exc}") from exc
    finally:
        if workbook is not None:
            workbook.close()


def _records(root, filename, sheet_name, required_headers):
    rows = _read_sheet(root, filename, sheet_name)
    if not rows:
        raise CommandError(f"Không có dữ liệu trong {filename} / {sheet_name}.")
    missing = set(required_headers) - set(rows[0]["values"])
    if missing:
        raise CommandError(
            f"{filename} / {sheet_name} thiếu cột: {', '.join(sorted(missing))}."
        )
    return rows


def _one_or_create(model, lookup, defaults, verify_fields, label):
    matches = list(model.objects.filter(**lookup)[:2])
    if len(matches) > 1:
        raise CommandError(f"{label}: tìm thấy nhiều bản ghi cho khóa {lookup}.")
    if matches:
        instance = matches[0]
        for field in verify_fields:
            expected = defaults.get(field)
            if field.endswith("_id") and field not in defaults:
                related = defaults.get(field[:-3])
                expected = getattr(related, "pk", related)
            if getattr(instance, field) != expected:
                raise CommandError(
                    f"{label}: bản ghi hiện có không khớp dữ liệu mẫu "
                    f"(trường {field}, khóa {lookup})."
                )
        return instance, False

    instance = model(**lookup, **defaults)
    try:
        instance.full_clean()
        instance.save()
    except ValidationError as exc:
        raise CommandError(f"{label}: dữ liệu không hợp lệ: {exc}") from exc
    return instance, True


class Command(BaseCommand):
    help = "Nhập bộ dữ liệu mẫu liên kết từ sample_bulk_data vào LMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=Path,
            default=DEFAULT_SOURCE,
            help="Thư mục chứa các file Excel mẫu.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chạy kiểm tra trong transaction rồi rollback, không lưu dữ liệu.",
        )

    def handle(self, *args, **options):
        root = options["source"].expanduser().resolve()
        if not root.is_dir():
            raise CommandError(f"Không tìm thấy thư mục dữ liệu: {root}")
        missing = [name for name in REQUIRED_WORKBOOKS if not (root / name).is_file()]
        if missing:
            raise CommandError(
                f"Thiếu các file Excel trong {root}: {', '.join(missing)}."
            )

        self.created = {}
        self.existing = {}
        try:
            with transaction.atomic():
                self._import_all(root)
                if options["dry_run"]:
                    transaction.set_rollback(True)
        except (IntegrityError, ValidationError) as exc:
            raise CommandError(
                f"Import thất bại; transaction đã rollback: {exc}"
            ) from exc

        action = "Dry-run hợp lệ, không lưu dữ liệu." if options["dry_run"] else "Import hoàn tất."
        self.stdout.write(self.style.SUCCESS(action))
        for label in sorted(set(self.created) | set(self.existing)):
            self.stdout.write(
                f"- {label}: tạo {self.created.get(label, 0)}, "
                f"đã có {self.existing.get(label, 0)}"
            )

    def _get_or_create(self, model, lookup, defaults, verify_fields, label):
        instance, created = _one_or_create(
            model, lookup, defaults, verify_fields, label
        )
        target = self.created if created else self.existing
        target[label] = target.get(label, 0) + 1
        return instance

    def _import_all(self, root):
        course_rows = _records(
            root, "05_Lop_hoc_va_ghi_danh.xlsx", "Khoa_hoc",
            ("Mã khóa học", "Tên khóa học", "Ngưỡng đạt (%)"),
        )
        courses = {}
        for row in course_rows:
            data = row["values"]
            code = _text(data["Mã khóa học"], "Mã khóa học")
            threshold = _decimal(data["Ngưỡng đạt (%)"], "Ngưỡng đạt (%)")
            courses[code] = self._get_or_create(
                Course,
                {"name": _text(data["Tên khóa học"], "Tên khóa học")},
                {"pass_threshold": threshold},
                ("pass_threshold",),
                "Khóa học",
            )

        learner_rows = _records(
            root, "01_Hoc_vien_1000.xlsx", "Hoc_vien",
            ("Họ và tên", "Ngày sinh", "Số CCCD", "Số điện thoại", "Email"),
        )
        learners = {}
        for row in learner_rows:
            data = row["values"]
            id_number = _text(data["Số CCCD"], "Số CCCD")
            defaults = {
                "full_name": _text(data["Họ và tên"], "Họ và tên"),
                "date_of_birth": _date(data["Ngày sinh"], "Ngày sinh"),
                "phone": _text(data["Số điện thoại"], "Số điện thoại"),
                "email": _text(data["Email"], "Email"),
            }
            learners[id_number] = self._get_or_create(
                Learner,
                {"id_number": id_number},
                defaults,
                ("full_name", "date_of_birth", "phone", "email"),
                "Học viên",
            )
        if len(learners) != 1000:
            raise CommandError(f"File học viên phải có 1000 CCCD duy nhất, hiện có {len(learners)}.")

        teacher_rows = _records(
            root, "06_Giao_vien_va_phan_cong.xlsx", "Giao_vien",
            ("Mã giáo viên", "Họ và tên", "Số điện thoại", "Email"),
        )
        teachers = {}
        for row in teacher_rows:
            data = row["values"]
            code = _text(data["Mã giáo viên"], "Mã giáo viên")
            email = _text(data["Email"], "Email")
            teachers[code] = self._get_or_create(
                Teacher,
                {"email": email},
                {
                    "full_name": _text(data["Họ và tên"], "Họ và tên"),
                    "phone": _text(data["Số điện thoại"], "Số điện thoại"),
                    "user": None,
                },
                ("full_name", "phone"),
                "Giáo viên",
            )

        class_rows = _records(
            root, "05_Lop_hoc_va_ghi_danh.xlsx", "Lop_hoc",
            ("Mã lớp", "Tên lớp", "Mã khóa học", "learning_format", "Lịch học",
             "Ngày khai giảng", "Sĩ số tối đa", "status"),
        )
        classes = {}
        for row in class_rows:
            data = row["values"]
            code = _text(data["Mã lớp"], "Mã lớp")
            course_code = _text(data["Mã khóa học"], "Mã khóa học")
            course = self._required_lookup(courses, course_code, "Khóa học")
            defaults = {
                "course": course,
                "learning_format": _text(data["learning_format"], "learning_format"),
                "schedule": _text(data["Lịch học"], "Lịch học"),
                "start_date": _date(data["Ngày khai giảng"], "Ngày khai giảng"),
                "max_students": int(data["Sĩ số tối đa"]),
                "status": _text(data["status"], "status"),
                "close_reason": "",
            }
            classes[code] = self._get_or_create(
                TrainingClass,
                {"name": _text(data["Tên lớp"], "Tên lớp")},
                defaults,
                ("course_id", "learning_format", "schedule", "start_date",
                 "max_students", "status"),
                "Lớp học",
            )

        enrollment_rows = _records(
            root, "05_Lop_hoc_va_ghi_danh.xlsx", "Ghi_danh",
            ("Mã lớp", "Số CCCD", "Họ và tên"),
        )
        for row in enrollment_rows:
            data = row["values"]
            id_number = _text(data["Số CCCD"], "Số CCCD")
            learner = self._required_lookup(learners, id_number, "Học viên")
            training_class = self._required_lookup(
                classes, _text(data["Mã lớp"], "Mã lớp"), "Lớp học"
            )
            if learner.full_name != _text(data["Họ và tên"], "Họ và tên"):
                raise CommandError(f"CCCD {id_number} có tên không khớp trong sheet ghi danh.")
            self._get_or_create(
                Enrollment,
                {"training_class": training_class, "learner": learner},
                {},
                (),
                "Ghi danh",
            )

        assignment_rows = _records(
            root, "06_Giao_vien_va_phan_cong.xlsx", "Phan_cong",
            ("Mã lớp", "Mã giáo viên", "Giáo viên"),
        )
        for row in assignment_rows:
            data = row["values"]
            training_class = self._required_lookup(
                classes, _text(data["Mã lớp"], "Mã lớp"), "Lớp học"
            )
            teacher_code = _text(data["Mã giáo viên"], "Mã giáo viên")
            teacher = self._required_lookup(teachers, teacher_code, "Giáo viên")
            if teacher.full_name != _text(data["Giáo viên"], "Giáo viên"):
                raise CommandError(f"Mã giáo viên {teacher_code} có tên không khớp.")
            self._get_or_create(
                ClassAssignment,
                {"training_class": training_class, "teacher": teacher, "is_active": True},
                {"removal_reason": ""},
                (),
                "Phân công giáo viên",
            )

        period_rows = _records(
            root, "03_Dot_thi_5.xlsx", "Dot_thi",
            ("Mã đợt thi", "Tên đợt thi", "Mã khóa chứng chỉ", "Ngày thi",
             "Hạn đăng ký", "Số thí sinh tối đa", "Thang điểm", "Trạng thái"),
        )
        periods = {}
        for row in period_rows:
            data = row["values"]
            code = _text(data["Mã đợt thi"], "Mã đợt thi")
            course = self._required_lookup(
                courses, _text(data["Mã khóa chứng chỉ"], "Mã khóa chứng chỉ"),
                "Khóa học",
            )
            periods[code] = self._get_or_create(
                ExamPeriod,
                {"name": _text(data["Tên đợt thi"], "Tên đợt thi")},
                {
                    "cert_type": course,
                    "exam_date": _date(data["Ngày thi"], "Ngày thi"),
                    "registration_deadline": _date(data["Hạn đăng ký"], "Hạn đăng ký"),
                    "max_candidates": int(data["Số thí sinh tối đa"]),
                    "score_scale": _decimal(data["Thang điểm"], "Thang điểm"),
                    "status": _text(data["Trạng thái"], "Trạng thái"),
                },
                ("cert_type_id", "exam_date", "registration_deadline",
                 "max_candidates", "score_scale", "status"),
                "Đợt thi",
            )

        application_rows = _records(
            root, "02_Ho_so_thi_sinh_1000.xlsx", "Tong_hop",
            ("Tên đợt thi", "Họ và tên", "Số CCCD/CMND", "Số điện thoại", "Email"),
        )
        applications = {}
        periods_by_name = {period.name: (code, period) for code, period in periods.items()}
        for row in application_rows:
            data = row["values"]
            period_name = _text(data["Tên đợt thi"], "Tên đợt thi")
            period_code, period = self._required_lookup(
                periods_by_name, period_name, "Đợt thi"
            )
            id_number = _text(data["Số CCCD/CMND"], "Số CCCD/CMND")
            learner = self._required_lookup(learners, id_number, "Học viên")
            full_name = _text(data["Họ và tên"], "Họ và tên")
            email = _text(data["Email"], "Email")
            if learner.full_name != full_name or learner.email != email:
                raise CommandError(f"Hồ sơ thí sinh CCCD {id_number} không khớp hồ sơ học viên.")
            application = self._get_or_create(
                ExamApplication,
                {"exam_period": period, "id_number": id_number},
                {
                    "full_name": full_name,
                    "date_of_birth": learner.date_of_birth,
                    "phone": _text(data["Số điện thoại"], "Số điện thoại"),
                    "email": email,
                    "source": ExamApplication.Source.GOOGLE_FORM,
                    "status": ExamApplication.Status.VALID,
                },
                ("full_name", "date_of_birth", "phone", "email", "status"),
                "Hồ sơ đăng ký thi",
            )
            applications[(period_code, id_number)] = application
        if len(application_rows) != 1000:
            raise CommandError(f"File hồ sơ thí sinh phải có 1000 dòng, hiện có {len(application_rows)}.")

        room_rows = _records(
            root, "04_Phong_thi_va_xep_phong.xlsx", "Phong_thi",
            ("Mã đợt thi", "Mã phòng", "Phòng thi", "Ca thi", "Sức chứa"),
        )
        rooms = {}
        for row in room_rows:
            data = row["values"]
            period_code = _text(data["Mã đợt thi"], "Mã đợt thi")
            period = self._required_lookup(periods, period_code, "Đợt thi")
            room_code = _text(data["Mã phòng"], "Mã phòng")
            rooms[(period_code, room_code)] = self._get_or_create(
                ExamRoom,
                {
                    "exam_period": period,
                    "name": _text(data["Phòng thi"], "Phòng thi"),
                },
                {
                    "session": _text(data["Ca thi"], "Ca thi"),
                    "capacity": int(data["Sức chứa"]),
                },
                ("session", "capacity"),
                "Phòng thi",
            )

        room_assignment_rows = _records(
            root, "04_Phong_thi_va_xep_phong.xlsx", "Xep_phong",
            ("Mã đợt thi", "Mã phòng", "Số CCCD", "Thí sinh"),
        )
        assignments = {}
        for row in room_assignment_rows:
            data = row["values"]
            period_code = _text(data["Mã đợt thi"], "Mã đợt thi")
            room_code = _text(data["Mã phòng"], "Mã phòng")
            id_number = _text(data["Số CCCD"], "Số CCCD")
            room = self._required_lookup(rooms, (period_code, room_code), "Phòng thi")
            application = self._required_lookup(
                applications, (period_code, id_number), "Hồ sơ thí sinh"
            )
            if application.full_name != _text(data["Thí sinh"], "Thí sinh"):
                raise CommandError(f"CCCD {id_number} có tên không khớp trong danh sách xếp phòng.")
            other_rooms = ExamRoomAssignment.objects.filter(
                application=application
            ).exclude(exam_room=room)
            if other_rooms.exists():
                raise CommandError(
                    f"Thí sinh CCCD {id_number} đã được xếp vào phòng khác trong đợt thi."
                )
            assignment = self._get_or_create(
                ExamRoomAssignment,
                {"exam_room": room, "application": application},
                {},
                (),
                "Xếp phòng",
            )
            assignments[(period_code, room_code, id_number)] = assignment

        score_rows = _records(
            root, "08_Diem_thi_1000.xlsx", "Tong_hop",
            ("Thí sinh", "CCCD", "Tên đợt thi", "Phòng thi", "Ca thi", "Điểm số"),
        )
        for row in score_rows:
            data = row["values"]
            id_number = _text(data["CCCD"], "CCCD")
            period_name = _text(data["Tên đợt thi"], "Tên đợt thi")
            period_code, _period = self._required_lookup(
                periods_by_name, period_name, "Đợt thi"
            )
            matching_room_codes = [
                code for (code_period, code), room in rooms.items()
                if code_period == period_code
                and room.name == _text(data["Phòng thi"], "Phòng thi")
                and room.session == _text(data["Ca thi"], "Ca thi")
            ]
            if len(matching_room_codes) != 1:
                raise CommandError(
                    f"Không xác định duy nhất phòng thi {data['Phòng thi']!r} "
                    f"cho đợt {period_name!r}."
                )
            assignment = self._required_lookup(
                assignments,
                (period_code, matching_room_codes[0], id_number),
                "Xếp phòng",
            )
            if assignment.application.full_name != _text(data["Thí sinh"], "Thí sinh"):
                raise CommandError(f"Điểm thi CCCD {id_number} có tên không khớp.")
            self._get_or_create(
                ExamScore,
                {"assignment": assignment},
                {
                    "score": _decimal(data["Điểm số"], "Điểm số"),
                    "is_absent": False,
                    "is_published": False,
                    "is_locked": False,
                },
                ("score", "is_absent"),
                "Điểm thi",
            )

        result_rows = _records(
            root, "07_Diem_cuoi_khoa_1000.xlsx", "Tong_hop",
            ("Lớp", "Họ và tên", "Số CCCD", "Điểm cuối khóa"),
        )
        for row in result_rows:
            data = row["values"]
            class_matches = [
                (code, training_class) for code, training_class in classes.items()
                if training_class.name == _text(data["Lớp"], "Lớp")
            ]
            if len(class_matches) != 1:
                raise CommandError(f"Không xác định duy nhất lớp {data['Lớp']!r}.")
            class_code, training_class = class_matches[0]
            id_number = _text(data["Số CCCD"], "Số CCCD")
            learner = self._required_lookup(learners, id_number, "Học viên")
            if learner.full_name != _text(data["Họ và tên"], "Họ và tên"):
                raise CommandError(f"Điểm cuối khóa CCCD {id_number} có tên không khớp.")
            if not Enrollment.objects.filter(
                training_class=training_class, learner=learner
            ).exists():
                raise CommandError(
                    f"Học viên CCCD {id_number} chưa được ghi danh vào lớp {training_class.name}."
                )
            self._get_or_create(
                FinalCourseResult,
                {"training_class": training_class, "learner": learner},
                {
                    "score": _decimal(data["Điểm cuối khóa"], "Điểm cuối khóa"),
                    "is_absent": False,
                    "is_published": False,
                },
                ("score", "is_absent"),
                "Điểm cuối khóa",
            )

        self._check_expected_counts(
            learner_rows=len(learner_rows),
            application_rows=len(application_rows),
            enrollment_rows=len(enrollment_rows),
            final_result_rows=len(result_rows),
            exam_score_rows=len(score_rows),
        )

    @staticmethod
    def _required_lookup(mapping, key, label):
        try:
            return mapping[key]
        except KeyError as exc:
            raise CommandError(f"{label}: không tìm thấy khóa tham chiếu {key!r}.") from exc

    @staticmethod
    def _check_expected_counts(
        learner_rows,
        application_rows,
        enrollment_rows,
        final_result_rows,
        exam_score_rows,
    ):
        expected = {
            "học viên": learner_rows,
            "hồ sơ thí sinh": application_rows,
            "ghi danh": enrollment_rows,
            "điểm cuối khóa": final_result_rows,
            "điểm thi": exam_score_rows,
        }
        for label, count in expected.items():
            if count != 1000:
                raise CommandError(
                    f"File {label} phải có 1000 dòng dữ liệu, hiện có {count}."
                )
