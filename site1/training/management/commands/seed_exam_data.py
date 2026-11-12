"""
Lệnh tạo dữ liệu demo phong phú cho module Khảo thí.
Chạy: python manage.py seed_exam_data
       python manage.py seed_exam_data --clear   (xóa cũ trước)
"""
import random
from decimal import Decimal
from datetime import date

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from training.models import (
    Course,
    ExamApplication,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
    MyBKExportLog,
)

# ─── Pool tên thí sinh (100 cái) ────────────────────────────────────────────
HO = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ",
    "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý",
    "Đinh", "Lưu", "Quách", "Mạc",
]
TEN = [
    "Minh Anh", "Gia Bảo", "Hoàng Phúc", "Thảo Vy", "Quốc Huy",
    "Ngọc Hà", "Tuấn Kiệt", "Khánh Linh", "Đức Anh", "Phương Thảo",
    "Thanh Tùng", "Hồng Nhung", "Trọng Nghĩa", "Thu Hương", "Việt Dũng",
    "Bích Ngọc", "Tiến Đạt", "Lan Anh", "Xuân Trường", "Mỹ Duyên",
    "Văn Hải", "Thị Hoa", "Quốc Toàn", "Ngọc Mai", "Hữu Lộc",
    "Kim Yến", "Chí Tâm", "Thu Thảo", "Đình Khoa", "Ánh Dương",
    "Viết Hùng", "Thanh Xuân", "Trường Giang", "Thúy An", "Huy Hoàng",
    "Phúc Lâm", "Thị Loan", "Bảo Châu", "Nhật Nam", "Yến Nhi",
    "Minh Khoa", "Hà Linh", "Trí Dũng", "Ngọc Trinh", "Thành Nhân",
    "Bảo Trân", "Đức Khải", "Vân Anh", "Thanh Hải", "Thu Ngân",
]

PHONE_PREFIXES = ["090", "091", "093", "094", "096", "097", "098", "032", "033", "034", "035", "036", "037", "038", "039", "070", "079", "077", "076", "078"]


def rnd_name(seed):
    random.seed(seed)
    return f"{random.choice(HO)} {random.choice(TEN)}"


def rnd_phone(seed):
    random.seed(seed * 7 + 13)
    return f"{random.choice(PHONE_PREFIXES)}{random.randint(1000000, 9999999)}"


def rnd_id(prefix, idx):
    return f"{prefix}{str(idx).zfill(9)}"[:12]


# ─── Cấu hình các đợt thi ───────────────────────────────────────────────────
PERIODS_CONFIG = [
    # (name, exam_date, reg_deadline, course_idx, max_cand, status, n_apps)
    {
        "name": "Đợt thi tháng 7/2026 — An toàn điện cơ bản",
        "exam_date": date(2026, 7, 20),
        "registration_deadline": date(2026, 7, 5),
        "course_idx": 0,
        "max_candidates": 60,
        "status": "closed",
        "n_apps": 52,
        "has_scores": True,
        "scores_published": True,
        "has_mybk_export": True,
        "has_duplicates": 4,
    },
    {
        "name": "Đợt thi tháng 9/2026 — Kỹ thuật máy tính",
        "exam_date": date(2026, 9, 14),
        "registration_deadline": date(2026, 8, 30),
        "course_idx": 1,
        "max_candidates": 45,
        "status": "closed",
        "n_apps": 38,
        "has_scores": True,
        "scores_published": True,
        "has_mybk_export": True,
        "has_duplicates": 2,
    },
    {
        "name": "Đợt thi tháng 10/2026 — Điện tử công nghiệp",
        "exam_date": date(2026, 10, 18),
        "registration_deadline": date(2026, 10, 3),
        "course_idx": 2,
        "max_candidates": 80,
        "status": "published",
        "n_apps": 44,
        "has_scores": True,
        "scores_published": True,
        "has_mybk_export": False,
        "has_duplicates": 3,
    },
    {
        "name": "Đợt thi tháng 11/2026 — An toàn thông tin",
        "exam_date": date(2026, 11, 22),
        "registration_deadline": date(2026, 11, 7),
        "course_idx": 3,
        "max_candidates": 100,
        "status": "published",
        "n_apps": 55,
        "has_scores": False,
        "scores_published": False,
        "has_mybk_export": False,
        "has_duplicates": 5,
    },
    {
        "name": "Đợt thi tháng 12/2026 — Mạng máy tính căn bản",
        "exam_date": date(2026, 12, 13),
        "registration_deadline": date(2026, 11, 28),
        "course_idx": 4,
        "max_candidates": 40,
        "status": "draft",
        "n_apps": 0,
        "has_scores": False,
        "scores_published": False,
        "has_mybk_export": False,
        "has_duplicates": 0,
    },
    {
        "name": "Đợt thi tháng 1/2027 — Lập trình Python ứng dụng",
        "exam_date": date(2027, 1, 18),
        "registration_deadline": date(2027, 1, 3),
        "course_idx": 5,
        "max_candidates": 35,
        "status": "draft",
        "n_apps": 0,
        "has_scores": False,
        "scores_published": False,
        "has_mybk_export": False,
        "has_duplicates": 0,
    },
    {
        "name": "Đợt thi tháng 6/2026 — Kỹ năng tin học văn phòng",
        "exam_date": date(2026, 6, 15),
        "registration_deadline": date(2026, 6, 1),
        "course_idx": 7,
        "max_candidates": 80,
        "status": "closed",
        "n_apps": 72,
        "has_scores": True,
        "scores_published": True,
        "has_mybk_export": True,
        "has_duplicates": 6,
    },
    {
        "name": "Đợt thi tháng 2/2026 — Cơ sở dữ liệu thực hành",
        "exam_date": date(2026, 2, 22),
        "registration_deadline": date(2026, 2, 7),
        "course_idx": 6,
        "max_candidates": 30,
        "status": "closed",
        "n_apps": 27,
        "has_scores": True,
        "scores_published": True,
        "has_mybk_export": True,
        "has_duplicates": 1,
    },
]

ROOM_NAMES = ["A101", "A102", "A103", "B201", "B202", "B203", "C301", "C302"]
SESSIONS = [
    "Sáng (8:00 - 10:00)",
    "Sáng (10:00 - 12:00)",
    "Chiều (13:30 - 15:30)",
    "Chiều (15:30 - 17:30)",
]


class Command(BaseCommand):
    help = "Tạo dữ liệu demo phong phú cho module Khảo thí"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Xóa dữ liệu exam cũ trước khi tạo mới",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            ExamScore.objects.all().delete()
            ExamRoomAssignment.objects.all().delete()
            ExamRoom.objects.all().delete()
            ExamApplication.objects.all().delete()
            MyBKExportLog.objects.all().delete()
            ExamPeriod.objects.all().delete()
            self.stdout.write(self.style.WARNING("✓ Đã xóa dữ liệu exam cũ."))

        # ── Tạo / lấy user Cán bộ thi ──────────────────────────────────────
        canbothi_group, _ = Group.objects.get_or_create(name="Cán bộ thi")
        canbothi, created = User.objects.get_or_create(
            username="cce_canbothi",
            defaults={"first_name": "Cán bộ", "last_name": "Thi"},
        )
        if created:
            canbothi.set_password("CCEtest@2026!")
            canbothi.save()
        canbothi.groups.add(canbothi_group)

        # ── Lấy Courses ─────────────────────────────────────────────────────
        courses = list(Course.objects.all())
        if not courses:
            self.stdout.write(self.style.ERROR(
                "Không có Course nào. Chạy 'seed_demo_data' trước."
            ))
            return

        # ── Tạo từng đợt thi ────────────────────────────────────────────────
        total_periods = 0
        total_rooms = 0
        total_apps = 0
        total_dups = 0
        total_scores = 0
        total_exports = 0

        for cfg in PERIODS_CONFIG:
            course_idx = cfg["course_idx"]
            course = courses[course_idx % len(courses)]

            status_map = {
                "draft": ExamPeriod.Status.DRAFT,
                "published": ExamPeriod.Status.PUBLISHED,
                "closed": ExamPeriod.Status.CLOSED,
            }

            period, p_created = ExamPeriod.objects.get_or_create(
                name=cfg["name"],
                defaults={
                    "exam_date": cfg["exam_date"],
                    "registration_deadline": cfg["registration_deadline"],
                    "cert_type": course,
                    "score_scale": Decimal("100"),
                    "max_candidates": cfg["max_candidates"],
                    "status": status_map[cfg["status"]],
                },
            )
            if p_created:
                total_periods += 1

            n_apps = 0 if cfg["status"] == "draft" else cfg["n_apps"]
            n_dup = 0 if cfg["status"] == "draft" else cfg.get("has_duplicates", 0)

            # ── Tạo hồ sơ đăng ký hợp lệ ───────────────────────────────────
            apps = []
            period_seed = abs(hash(cfg["name"])) % 10000
            for i in range(n_apps):
                seed = period_seed * 1000 + i
                name = rnd_name(seed)
                phone = rnd_phone(seed)
                id_num = rnd_id(str(period_seed % 10), i + 1)
                source_choice = ["online", "google_form", "partner", "direct"][i % 4]

                app, a_created = ExamApplication.objects.get_or_create(
                    exam_period=period,
                    id_number=id_num,
                    defaults={
                        "full_name": name,
                        "phone": phone,
                        "source": source_choice,
                        "status": ExamApplication.Status.VALID,
                    },
                )
                apps.append(app)
                if a_created:
                    total_apps += 1

            # ── Tạo hồ sơ trùng (duplicate) ─────────────────────────────────
            for d in range(n_dup):
                # Chọn một hồ sơ hợp lệ để tạo hồ sơ đăng ký trùng (cùng họ tên, CCCD, điện thoại)
                if not apps:
                    break
                orig = apps[d % len(apps)]
                dup, d_created = ExamApplication.objects.get_or_create(
                    exam_period=period,
                    id_number=orig.id_number,
                    full_name=orig.full_name,
                    status=ExamApplication.Status.DUPLICATE,
                    defaults={
                        "phone": orig.phone,
                        "source": ["online", "google_form", "partner", "direct"][d % 4],
                    },
                )
                if d_created:
                    total_dups += 1

            # ── Phân phòng thi ───────────────────────────────────────────────
            valid_apps = [a for a in apps if a.status == ExamApplication.Status.VALID]
            capacity = 20
            room_idx_local = 0
            room_app_map = {}  # room -> list of assignments

            # Với các đợt thi đã công bố (hoặc đang mở), chừa lại 15-20 thí sinh chưa xếp để test
            apps_to_assign = valid_apps
            if period.status == ExamPeriod.Status.PUBLISHED and len(valid_apps) > 25:
                apps_to_assign = valid_apps[:-20]

            for chunk_start in range(0, len(apps_to_assign), capacity):
                chunk = apps_to_assign[chunk_start:chunk_start + capacity]
                r_name = ROOM_NAMES[room_idx_local % len(ROOM_NAMES)]
                session = SESSIONS[room_idx_local % len(SESSIONS)]

                room, r_created = ExamRoom.objects.get_or_create(
                    exam_period=period,
                    name=r_name,
                    defaults={
                        "session": session,
                        "capacity": capacity,
                    },
                )
                if r_created:
                    total_rooms += 1

                asgns = []
                for app in chunk:
                    asgn, _ = ExamRoomAssignment.objects.get_or_create(
                        exam_room=room,
                        application=app,
                    )
                    asgns.append(asgn)
                room_app_map[room] = asgns
                room_idx_local += 1

            # ── Tạo điểm thi ────────────────────────────────────────────────
            if cfg["has_scores"]:
                is_pub = cfg["scores_published"]
                for room, asgns in room_app_map.items():
                    for idx_a, asgn in enumerate(asgns):
                        random.seed(abs(hash(str(asgn.pk))) % 99999)
                        is_absent = random.random() < 0.07  # 7% vắng
                        if is_absent:
                            sv = None
                        else:
                            # Phân phối điểm thực tế hơn
                            roll = random.random()
                            if roll < 0.05:
                                sv = Decimal(str(round(random.uniform(20, 49), 1)))   # Rớt nặng
                            elif roll < 0.20:
                                sv = Decimal(str(round(random.uniform(50, 59), 1)))   # Yếu
                            elif roll < 0.45:
                                sv = Decimal(str(round(random.uniform(60, 74), 1)))   # Trung bình
                            elif roll < 0.80:
                                sv = Decimal(str(round(random.uniform(75, 89), 1)))   # Khá
                            else:
                                sv = Decimal(str(round(random.uniform(90, 100), 1)))  # Giỏi

                        sc, s_created = ExamScore.objects.get_or_create(
                            assignment=asgn,
                            defaults={
                                "score": sv,
                                "is_absent": is_absent,
                                "is_published": is_pub,
                                "is_locked": is_pub,
                            },
                        )
                        if s_created:
                            total_scores += 1

            # ── Tạo log xuất MyBK ───────────────────────────────────────────
            if cfg.get("has_mybk_export") and valid_apps:
                log, l_created = MyBKExportLog.objects.get_or_create(
                    exam_period=period,
                    defaults={
                        "exported_by": canbothi,
                        "record_count": len(valid_apps),
                        "notes": f"Xuất {len(valid_apps)} hồ sơ — {cfg['name']}",
                    },
                )
                if l_created:
                    total_exports += 1

        # ── Tổng kết ────────────────────────────────────────────────────────
        # Thống kê thực tế trong DB
        db_periods = ExamPeriod.objects.count()
        db_apps = ExamApplication.objects.count()
        db_dups = ExamApplication.objects.filter(status=ExamApplication.Status.DUPLICATE).count()
        db_rooms = ExamRoom.objects.count()
        db_scores = ExamScore.objects.count()
        db_absent = ExamScore.objects.filter(is_absent=True).count()
        db_pub = ExamScore.objects.filter(is_published=True).count()
        db_exports = MyBKExportLog.objects.count()

        self.stdout.write(self.style.SUCCESS("✓ Exam demo data ready:"))
        self.stdout.write(f"  Đợt thi   : {db_periods} (draft/published/closed)")
        self.stdout.write(f"  Phòng thi  : {db_rooms}")
        self.stdout.write(f"  Hồ sơ     : {db_apps} ({db_dups} trùng lặp)")
        self.stdout.write(f"  Điểm thi   : {db_scores} ({db_absent} vắng, {db_pub} đã công bố)")
        self.stdout.write(f"  Log MyBK   : {db_exports}")
        self.stdout.write(f"  Login      : cce_canbothi / CCEtest@2026!")
