"""
Seed data phong phu, sat thuc te cho TẤT CẢ chuc nang he thong CCE.
Chay: python manage.py seed_all_data
       python manage.py seed_all_data --clear
"""
import random
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from training.models import (
    AuditLog,
    ClassAssignment,
    Course,
    Enrollment,
    ExamApplication,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
    Learner,
    MyBKExportLog,
    RoleFeaturePermission,
    Teacher,
    TrainingClass,
)
from training.audit import record_audit, snapshot_instance

# ═══════════════════════════════════════════════════════════════════════
# POOL TEN NGUOI VIET (UTF-8 OK trong data, chi tranh ma lop)
# ═══════════════════════════════════════════════════════════════════════

HO = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ",
    "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý",
    "Đinh", "Lưu", "Thái", "Triệu", "Châu", "Kiều", "Mạc", "Ninh",
    "Tạ", "Ông", "Quách", "Hứa", "Từ", "Mai",
]

TEN_NAM = [
    "Minh Anh", "Gia Bảo", "Hoàng Phúc", "Quốc Huy", "Tuấn Kiệt",
    "Đức Anh", "Thanh Tùng", "Trọng Nghĩa", "Việt Dũng", "Tiến Đạt",
    "Xuân Trường", "Văn Hải", "Quốc Toàn", "Hữu Lộc", "Chí Tâm",
    "Đình Khoa", "Viết Hùng", "Trường Giang", "Huy Hoàng", "Phúc Lâm",
    "Nhật Nam", "Minh Khoa", "Trí Dũng", "Thành Nhân", "Đức Khải",
    "Thanh Hải", "Bảo Long", "Minh Tuấn", "Gia Huy", "Thái Bình",
    "Hữu Nghĩa", "Quang Vinh", "Trần Khôi", "Xuân Bách", "Đức Việt",
    "Hồng Quân", "Mạnh Cường", "Tiến Vũ", "Thanh Phong", "Minh Hiếu",
]

TEN_NU = [
    "Thảo Vy", "Ngọc Hà", "Khánh Linh", "Phương Thảo", "Hồng Nhung",
    "Thu Hương", "Lan Anh", "Mỹ Duyên", "Thị Hoa", "Ngọc Mai",
    "Kim Yến", "Thu Thảo", "Ánh Dương", "Thanh Xuân", "Thúy An",
    "Thị Loan", "Bảo Châu", "Yến Nhi", "Hà Linh", "Ngọc Trinh",
    "Bảo Trân", "Vân Anh", "Thu Ngân", "Thúy Hằng", "Ngọc Linh",
    "Phương Linh", "Minh Châu", "Thị Dung", "Hoài An", "Thanh Vân",
    "Phương Uyên", "Hải Yến", "Trúc Ly", "Kim Ngân", "Diệu Linh",
    "Thùy Trang", "Minh Tâm", "Huyền Trang", "Thanh Thảo", "Mai Anh",
]

PHONE_PREFIXES = [
    "090", "091", "093", "094", "096", "097", "098",
    "032", "033", "034", "035", "036", "037", "038", "039",
    "070", "079", "077", "076", "078",
]

# ═══════════════════════════════════════════════════════════════════════
# COURSES — ten + ma viet tat ASCII + nguong dat
# Format ma lop: CCE-{MA}-{3so}  vi du CCE-ATDIEN-001
# ═══════════════════════════════════════════════════════════════════════

COURSE_DATA = [
    # (ten khoa hoc,            ma ASCII,  nguong dat)
    ("An toàn điện cơ bản",     "ATDIEN",   60),
    ("Kỹ thuật máy tính",       "KTMT",     65),
    ("Mạng máy tính căn bản",   "MMTCB",    60),
    ("Lập trình Python ứng dụng","LAPTR",   65),
    ("Cơ sở dữ liệu thực hành", "CSDL",     60),
    ("An toàn thông tin",       "ATTT",     70),
    ("Điện tử công nghiệp",     "DTCN",     60),
    ("Kỹ năng tin học văn phòng","TINHVP",  55),
    ("Điện lạnh dân dụng",      "DLDD",     60),
    ("Kỹ thuật PLC tự động hóa","PLCTDH",   65),
    ("Điện công nghiệp nâng cao","DCNNC",   65),
    ("Hệ thống nhúng IoT",      "HTNIOT",   70),
]

# ═══════════════════════════════════════════════════════════════════════
# GIAO VIEN — TS/ThS/PGS + email @cce.hcmut.edu.vn
# ═══════════════════════════════════════════════════════════════════════

TEACHER_DATA = [
    ("TS. Nguyễn Văn Hùng",       "0901234567", "nv.hung@cce.hcmut.edu.vn"),
    ("ThS. Trần Thị Lan",         "0912345678", "tt.lan@cce.hcmut.edu.vn"),
    ("TS. Lê Quốc Cường",         "0923456789", "lq.cuong@cce.hcmut.edu.vn"),
    ("ThS. Phạm Thị Dung",        "0934567890", "pt.dung@cce.hcmut.edu.vn"),
    ("PGS.TS. Hoàng Văn Nam",     "0945678901", "hv.nam@cce.hcmut.edu.vn"),
    ("ThS. Vũ Thị Hoa",           "0956789012", "vt.hoa@cce.hcmut.edu.vn"),
    ("TS. Đặng Quốc Hùng",        "0967890123", "dq.hung@cce.hcmut.edu.vn"),
    ("ThS. Bùi Thị Lan",          "0978901234", "bt.lan@cce.hcmut.edu.vn"),
    ("TS. Đỗ Văn Minh",           "0989012345", "dv.minh@cce.hcmut.edu.vn"),
    ("ThS. Ngô Thị Nga",          "0990123456", "nt.nga@cce.hcmut.edu.vn"),
    ("TS. Lý Văn Phong",          "0901111222", "lv.phong@cce.hcmut.edu.vn"),
    ("ThS. Trịnh Thị Quỳnh",      "0912222333", "tt.quynh@cce.hcmut.edu.vn"),
    ("PGS.TS. Đinh Văn Sơn",      "0923333444", "dv.son@cce.hcmut.edu.vn"),
    ("ThS. Phan Thị Tuyết",       "0934444555", "pt.tuyet@cce.hcmut.edu.vn"),
    ("TS. Cao Văn Uy",            "0945555666", "cv.uy@cce.hcmut.edu.vn"),
    ("ThS. Lưu Thị Vân",          "0956666777", "lt.van@cce.hcmut.edu.vn"),
    ("TS. Hồ Quang Vinh",         "0967777888", "hq.vinh@cce.hcmut.edu.vn"),
    ("ThS. Dương Thị Xuân",       "0978888999", "dt.xuan@cce.hcmut.edu.vn"),
    ("TS. Từ Văn Yên",            "0989999000", "tv.yen@cce.hcmut.edu.vn"),
    ("ThS. Mai Thị Zara",         "0900001111", "mt.zara@cce.hcmut.edu.vn"),
]

# ═══════════════════════════════════════════════════════════════════════
# LICH HOC thuc te
# ═══════════════════════════════════════════════════════════════════════

SCHEDULE_OPTIONS = [
    "Thứ 2 & Thứ 4, 07:30–09:30",
    "Thứ 2 & Thứ 4, 09:30–11:30",
    "Thứ 2 & Thứ 4, 13:30–15:30",
    "Thứ 2 & Thứ 4, 15:30–17:30",
    "Thứ 3 & Thứ 5, 07:30–09:30",
    "Thứ 3 & Thứ 5, 09:30–11:30",
    "Thứ 3 & Thứ 5, 13:30–15:30",
    "Thứ 3 & Thứ 5, 15:30–17:30",
    "Thứ 6 & Thứ 7, 07:30–09:30",
    "Thứ 6 & Thứ 7, 13:30–15:30",
    "Thứ 7 & Chủ nhật, 07:30–10:30 (tập trung)",
    "Thứ 7 & Chủ nhật, 13:30–16:30 (tập trung)",
    "Thứ 2, Thứ 4 & Thứ 6, 17:30–19:30 (tối)",
    "Thứ 3, Thứ 5 & Thứ 7, 17:30–19:30 (tối)",
]

CLOSE_REASONS = [
    "Lớp đã hoàn thành chương trình đào tạo theo kế hoạch học kỳ 1/2026.",
    "Học viên hoàn tất 100% số buổi học, đủ điều kiện dự thi cấp chứng chỉ.",
    "Kết thúc chu kỳ đào tạo kỳ 2/2025, lớp giải thể theo quyết định của CCE.",
    "Giải thể lớp do không đủ sĩ số tối thiểu (dưới 5 học viên) theo quy định.",
    "Hoàn thành đào tạo theo hợp đồng với Công ty CP Điện lực Miền Nam.",
    "Lớp kết thúc theo kế hoạch, học viên chuyển sang lớp ôn thi.",
    "Hoàn thành chương trình hợp tác đào tạo với Trung tâm Giáo dục nghề nghiệp Q.10.",
]

# ═══════════════════════════════════════════════════════════════════════
# QUYEN CHUC NANG theo vai tro
# ═══════════════════════════════════════════════════════════════════════

FEATURES = [
    "Quản lý khóa học",
    "Quản lý lớp học",
    "Quản lý giảng viên",
    "Quản lý học viên",
    "Ghi danh lớp học",
    "Phân công giảng viên",
    "Đóng lớp học",
    "Quản lý đợt thi",
    "Hồ sơ đăng ký dự thi",
    "Import hồ sơ",
    "Xử lý trùng hồ sơ",
    "Quản lý phòng thi",
    "Phân phòng thí sinh",
    "Nhập điểm",
    "Upload điểm",
    "Công bố điểm",
    "Xuất dữ liệu MyBK",
    "Thống kê khảo thí",
    "Báo cáo giáo vụ",
    "Nhật ký kiểm toán",
    "Phân quyền vai trò",
]

ROLE_PERMISSIONS = {
    "Cán bộ thi": {
        "Quản lý khóa học": False, "Quản lý lớp học": False,
        "Quản lý giảng viên": False, "Quản lý học viên": False,
        "Ghi danh lớp học": False, "Phân công giảng viên": False,
        "Đóng lớp học": False,
        "Quản lý đợt thi": True, "Hồ sơ đăng ký dự thi": True,
        "Import hồ sơ": True, "Xử lý trùng hồ sơ": True,
        "Quản lý phòng thi": True, "Phân phòng thí sinh": True,
        "Nhập điểm": True, "Upload điểm": True, "Công bố điểm": True,
        "Xuất dữ liệu MyBK": True, "Thống kê khảo thí": True,
        "Báo cáo giáo vụ": False, "Nhật ký kiểm toán": True,
        "Phân quyền vai trò": False,
    },
    "Giáo vụ": {
        "Quản lý khóa học": True, "Quản lý lớp học": True,
        "Quản lý giảng viên": True, "Quản lý học viên": True,
        "Ghi danh lớp học": True, "Phân công giảng viên": True,
        "Đóng lớp học": True,
        "Quản lý đợt thi": False, "Hồ sơ đăng ký dự thi": False,
        "Import hồ sơ": False, "Xử lý trùng hồ sơ": False,
        "Quản lý phòng thi": False, "Phân phòng thí sinh": False,
        "Nhập điểm": False, "Upload điểm": False, "Công bố điểm": False,
        "Xuất dữ liệu MyBK": False, "Thống kê khảo thí": False,
        "Báo cáo giáo vụ": True, "Nhật ký kiểm toán": True,
        "Phân quyền vai trò": False,
    },
    "Giáo viên": {f: False for f in FEATURES},
    "Quản trị viên": {f: True for f in FEATURES},
}

# ═══════════════════════════════════════════════════════════════════════
# DOT THI
# ═══════════════════════════════════════════════════════════════════════

EXAM_PERIODS_CONFIG = [
    {"name": "Đợt thi CCE tháng 02/2026 — Cơ sở dữ liệu thực hành",
     "exam_date": date(2026, 2, 22), "reg": date(2026, 2, 7),
     "ci": 4, "max": 30, "status": "closed", "n": 27, "dup": 1,
     "scores": True, "pub": True, "mybk": True},
    {"name": "Đợt thi CCE tháng 04/2026 — An toàn điện cơ bản",
     "exam_date": date(2026, 4, 19), "reg": date(2026, 4, 4),
     "ci": 0, "max": 60, "status": "closed", "n": 52, "dup": 4,
     "scores": True, "pub": True, "mybk": True},
    {"name": "Đợt thi CCE tháng 06/2026 — Kỹ năng tin học văn phòng",
     "exam_date": date(2026, 6, 14), "reg": date(2026, 5, 30),
     "ci": 7, "max": 80, "status": "closed", "n": 72, "dup": 6,
     "scores": True, "pub": True, "mybk": True},
    {"name": "Đợt thi CCE tháng 07/2026 — Điện tử công nghiệp",
     "exam_date": date(2026, 7, 19), "reg": date(2026, 7, 4),
     "ci": 6, "max": 60, "status": "closed", "n": 48, "dup": 3,
     "scores": True, "pub": True, "mybk": True},
    {"name": "Đợt thi CCE tháng 09/2026 — Kỹ thuật máy tính",
     "exam_date": date(2026, 9, 13), "reg": date(2026, 8, 29),
     "ci": 1, "max": 45, "status": "closed", "n": 38, "dup": 2,
     "scores": True, "pub": True, "mybk": True},
    {"name": "Đợt thi CCE tháng 10/2026 — Điện lạnh dân dụng",
     "exam_date": date(2026, 10, 25), "reg": date(2026, 10, 10),
     "ci": 8, "max": 40, "status": "published", "n": 35, "dup": 2,
     "scores": True, "pub": True, "mybk": False},
    {"name": "Đợt thi CCE tháng 11/2026 — An toàn thông tin",
     "exam_date": date(2026, 11, 22), "reg": date(2026, 11, 7),
     "ci": 5, "max": 60, "status": "published", "n": 55, "dup": 5,
     "scores": False, "pub": False, "mybk": False},
    {"name": "Đợt thi CCE tháng 12/2026 — Mạng máy tính căn bản",
     "exam_date": date(2026, 12, 13), "reg": date(2026, 11, 28),
     "ci": 2, "max": 50, "status": "draft", "n": 30, "dup": 2,
     "scores": False, "pub": False, "mybk": False},
    {"name": "Đợt thi CCE tháng 01/2027 — Lập trình Python ứng dụng",
     "exam_date": date(2027, 1, 18), "reg": date(2027, 1, 3),
     "ci": 3, "max": 35, "status": "draft", "n": 15, "dup": 1,
     "scores": False, "pub": False, "mybk": False},
    {"name": "Đợt thi CCE tháng 02/2027 — Kỹ thuật PLC tự động hóa",
     "exam_date": date(2027, 2, 21), "reg": date(2027, 2, 6),
     "ci": 9, "max": 25, "status": "draft", "n": 8, "dup": 0,
     "scores": False, "pub": False, "mybk": False},
]

ROOM_NAMES = [
    "A1.01", "A1.02", "A1.03", "A1.04",
    "B2.01", "B2.02", "B2.03",
    "C3.01", "C3.02", "D4.01",
]
SESSIONS = [
    "Ca 1 — Sáng (07:30–09:30)",
    "Ca 2 — Sáng (09:30–11:30)",
    "Ca 3 — Chiều (13:30–15:30)",
    "Ca 4 — Chiều (15:30–17:30)",
]

MYBK_NOTES = [
    "Xuất lần 1 — dữ liệu sơ bộ sau khi tổng hợp điểm.",
    "Xuất chính thức — đã kiểm tra và xác nhận đủ điều kiện cấp chứng chỉ.",
    "Xuất bổ sung — cập nhật danh sách thí sinh vắng mặt có lý do.",
    "Xuất tổng hợp theo yêu cầu của Phòng Đào tạo ĐH Bách Khoa.",
    "Xuất phục vụ in chứng chỉ — duyệt bởi Trưởng bộ phận Khảo thí.",
]


# ═══════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def rnd_name(seed, female_prob=0.45):
    random.seed(seed)
    h = random.choice(HO)
    t = random.choice(TEN_NU if random.random() < female_prob else TEN_NAM)
    return f"{h} {t}"


def rnd_phone(seed):
    random.seed(seed * 7 + 13)
    prefix = random.choice(PHONE_PREFIXES)
    num = random.randint(1000000, 9999999)
    return f"{prefix}{num}"


def rnd_cccd(period_seed, idx):
    """Sinh CCCD 12 so thuc te: 0XX YYYY ZZZZZZ"""
    random.seed(period_seed * 1000 + idx + 42)
    # 3 so tinh/thanh pho (001–096)
    tinh = str(random.randint(1, 96)).zfill(3)
    # 1 so gioi tinh + nam sinh (0/1 + 2dig nam): 0=nu<=2000, 1=nu>2000, 2=nam<=2000, 3=nam>2000
    gioi_nam = random.randint(0, 1)
    year = random.randint(1970, 2005)
    gns = (2 + gioi_nam) if year <= 1999 else (gioi_nam)
    yy = str(year % 100).zfill(2)
    # 6 so ngau nhien
    tail = str(random.randint(100000, 999999))
    return f"{tinh}{gns}{yy}{tail}"[:12]


def rnd_score():
    r = random.random()
    if r < 0.04:
        return Decimal(str(round(random.uniform(20, 49), 1)))   # Truot
    elif r < 0.18:
        return Decimal(str(round(random.uniform(50, 59), 1)))   # Yeu
    elif r < 0.45:
        return Decimal(str(round(random.uniform(60, 74), 1)))   # Trung binh
    elif r < 0.80:
        return Decimal(str(round(random.uniform(75, 89), 1)))   # Kha
    else:
        return Decimal(str(round(random.uniform(90, 100), 1)))  # Gioi


def class_code(course_code, seq):
    """Tao ma lop: CCE-ATDIEN-001"""
    return f"CCE-{course_code}-{seq:03d}"


# ═══════════════════════════════════════════════════════════════════════
# COMMAND
# ═══════════════════════════════════════════════════════════════════════

class Command(BaseCommand):
    help = "Seed data phu hop thuc te cho TAT CA chuc nang he thong CCE"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true",
            help="Xoa toan bo data cu truoc khi tao moi",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self._clear_all()

        actor = self._ensure_users()
        courses = self._seed_courses()
        teachers = self._seed_teachers()
        learners = self._seed_learners(200)
        classes = self._seed_classes(courses, teachers)
        self._seed_enrollments(classes, learners)
        self._seed_role_permissions()
        self._seed_audit_log(actor, courses, classes, learners, teachers)
        self._seed_exam_data(courses, actor)
        self._print_summary()

    # ─────────────────────────────────────────────────────────────────
    def _clear_all(self):
        self.stdout.write(self.style.WARNING("Dang xoa data cu..."))
        for Model in [
            ExamScore, ExamRoomAssignment, ExamRoom,
            ExamApplication, MyBKExportLog, ExamPeriod,
            AuditLog, RoleFeaturePermission,
            Enrollment, ClassAssignment, TrainingClass,
            Learner, Course,
        ]:
            Model.objects.all().delete()
        Teacher.objects.filter(user__isnull=True).delete()
        self.stdout.write(self.style.WARNING("✓ Da xoa xong."))

    # ─────────────────────────────────────────────────────────────────
    def _ensure_users(self):
        DEMO = [
            ("Cán bộ thi",    "cce_canbothi",  "CCEtest@2026!", "Cán bộ",  "Thi",   False, False),
            ("Giáo vụ",       "cce_giaovu",    "CCEtest@2026!", "Giáo",    "Vụ",    False, False),
            ("Giáo viên",     "cce_giaovien",  "CCEtest@2026!", "Giáo",    "Viên",  False, False),
            ("Quản trị viên", "cce_admin",     "CCEtest@2026!", "Quản",    "Trị",   True,  True),
        ]
        actor = None
        for grp_name, uname, pwd, fn, ln, is_staff, is_su in DEMO:
            grp, _ = Group.objects.get_or_create(name=grp_name)
            user, created = User.objects.get_or_create(
                username=uname,
                defaults={"first_name": fn, "last_name": ln,
                          "is_staff": is_staff, "is_superuser": is_su},
            )
            if created:
                user.set_password(pwd)
                user.save()
            user.groups.add(grp)
            if grp_name == "Giáo vụ":
                actor = user
        return actor

    # ─────────────────────────────────────────────────────────────────
    def _seed_courses(self):
        courses = []
        for name, code, threshold in COURSE_DATA:
            c, _ = Course.objects.get_or_create(
                name=name,
                defaults={"pass_threshold": Decimal(str(threshold))},
            )
            # Gan them thuoc tinh course_code (luu vao bien local, khong phai field)
            c._code = code
            courses.append(c)
        self.stdout.write(f"  Courses: {len(courses)}")
        return courses

    # ─────────────────────────────────────────────────────────────────
    def _seed_teachers(self):
        teachers = []
        for name, phone, email in TEACHER_DATA:
            t, _ = Teacher.objects.get_or_create(
                email=email,
                defaults={"full_name": name, "phone": phone},
            )
            teachers.append(t)
        self.stdout.write(f"  Teachers: {len(teachers)}")
        return teachers

    # ─────────────────────────────────────────────────────────────────
    def _seed_learners(self, n=200):
        learners = []
        for i in range(n):
            seed = i * 31 + 7
            name = rnd_name(seed)
            email = f"learner{i + 1:03d}@sinh.vien.test"
            phone = rnd_phone(seed)
            lrn, _ = Learner.objects.get_or_create(
                email=email,
                defaults={"full_name": name, "phone": phone},
            )
            learners.append(lrn)
        self.stdout.write(f"  Learners: {len(learners)}")
        return learners

    # ─────────────────────────────────────────────────────────────────
    def _seed_classes(self, courses, teachers):
        """
        Tao lop hoc voi ma dung chuan: CCE-ATDIEN-001
        Moi khoa hoc co 3–4 lop, so thu tu rieng.
        """
        classes = []
        counters = {}   # course_code -> so thu tu

        # 40 lop: lap vong qua courses
        for i in range(40):
            course = courses[i % len(courses)]
            code = course._code
            counters[code] = counters.get(code, 0) + 1
            seq = counters[code]

            is_online = i % 4 == 1   # 25% online
            status = (
                TrainingClass.Status.CLOSED if i % 5 == 0
                else TrainingClass.Status.OPEN
            )
            schedule = SCHEDULE_OPTIONS[i % len(SCHEDULE_OPTIONS)]
            max_s = random.choice([20, 25, 30, 35, 40])

            cls, _ = TrainingClass.objects.get_or_create(
                name=class_code(code, seq),
                defaults={
                    "course": course,
                    "learning_format": (
                        TrainingClass.LearningFormat.ONLINE if is_online
                        else TrainingClass.LearningFormat.AT_CENTER
                    ),
                    "schedule": schedule,
                    "max_students": max_s,
                    "status": status,
                    "close_reason": (
                        CLOSE_REASONS[i % len(CLOSE_REASONS)]
                        if status == TrainingClass.Status.CLOSED else ""
                    ),
                },
            )
            # Phan cong giao vien chinh
            t1 = teachers[i % len(teachers)]
            ClassAssignment.objects.get_or_create(
                training_class=cls, teacher=t1,
                defaults={"is_active": True},
            )
            # Phan cong giao vien phu (1 trong 3 lop)
            if i % 3 == 0:
                t2 = teachers[(i + 3) % len(teachers)]
                if t2 != t1:
                    ClassAssignment.objects.get_or_create(
                        training_class=cls, teacher=t2,
                        defaults={"is_active": True},
                    )
            classes.append(cls)

        self.stdout.write(f"  TrainingClasses: {TrainingClass.objects.count()}")
        self.stdout.write(f"  ClassAssignments: {ClassAssignment.objects.count()}")
        return classes

    # ─────────────────────────────────────────────────────────────────
    def _seed_enrollments(self, classes, learners):
        open_classes = [c for c in classes if c.status == TrainingClass.Status.OPEN]
        for i, cls in enumerate(open_classes):
            target = min(cls.max_students - 2, random.randint(10, 22))
            enrolled = 0
            start = (i * 11) % len(learners)
            for j in range(target * 3):
                if enrolled >= target:
                    break
                if cls.enrollments.count() >= cls.max_students:
                    break
                lrn = learners[(start + j) % len(learners)]
                _, ok = Enrollment.objects.get_or_create(
                    training_class=cls, learner=lrn,
                )
                if ok:
                    enrolled += 1
        self.stdout.write(f"  Enrollments: {Enrollment.objects.count()}")

    # ─────────────────────────────────────────────────────────────────
    def _seed_role_permissions(self):
        for grp_name, perms in ROLE_PERMISSIONS.items():
            grp, _ = Group.objects.get_or_create(name=grp_name)
            for feature, allowed in perms.items():
                RoleFeaturePermission.objects.get_or_create(
                    group=grp, feature=feature,
                    defaults={"allowed": allowed},
                )
        self.stdout.write(f"  RoleFeaturePermissions: {RoleFeaturePermission.objects.count()}")

    # ─────────────────────────────────────────────────────────────────
    def _seed_audit_log(self, actor, courses, classes, learners, teachers):
        if not actor:
            return
        created = 0

        # CREATE
        for obj in [*courses[:6], *classes[:10], *learners[:12], *teachers[:6]]:
            if not AuditLog.objects.filter(
                action=AuditLog.Action.CREATE,
                object_type=obj._meta.label,
                object_id=str(obj.pk),
            ).exists():
                record_audit(
                    actor, AuditLog.Action.CREATE, obj,
                    new_values=snapshot_instance(obj),
                    reason="Khởi tạo dữ liệu hệ thống — CCE 2026",
                )
                created += 1

        # EDIT
        edit_reasons = [
            "Cập nhật lịch học theo yêu cầu học viên.",
            "Điều chỉnh sĩ số tối đa theo phòng học mới.",
            "Cập nhật thông tin giảng viên thay thế.",
        ]
        for idx, cls in enumerate(classes[:8]):
            if not AuditLog.objects.filter(
                action=AuditLog.Action.EDIT,
                object_type=cls._meta.label,
                object_id=str(cls.pk),
            ).exists():
                old = {"schedule": "Thứ 2 & Thứ 4, 08:00–10:00", "max_students": 20}
                record_audit(
                    actor, AuditLog.Action.EDIT, cls,
                    old_values=old,
                    new_values=snapshot_instance(cls),
                    object_label=str(cls),
                    reason=edit_reasons[idx % len(edit_reasons)],
                )
                created += 1

        # PUBLISH
        for cls in classes[:5]:
            if not AuditLog.objects.filter(
                action=AuditLog.Action.PUBLISH,
                object_type=cls._meta.label,
                object_id=str(cls.pk),
            ).exists():
                record_audit(
                    actor, AuditLog.Action.PUBLISH, cls,
                    object_label=str(cls),
                    reason="Mở đăng ký lớp học theo kế hoạch đào tạo kỳ 2/2026.",
                )
                created += 1

        # EXPORT (bao cao dinh ky)
        export_labels = [
            "Báo cáo danh sách lớp học kỳ 1/2026",
            "Báo cáo tổng hợp ghi danh tháng 6/2026",
            "Xuất danh sách học viên hoàn thành khóa học",
            "Báo cáo thống kê giảng viên — Q2/2026",
            "Xuất dữ liệu phục vụ kiểm toán nội bộ",
        ]
        for i, label in enumerate(export_labels):
            AuditLog.objects.create(
                actor=actor,
                actor_username=actor.username,
                action=AuditLog.Action.EXPORT,
                object_type="training.TrainingClass",
                object_id="",
                object_label=label,
                new_values={"format": "xlsx", "records": random.randint(15, 60)},
                reason="Xuất báo cáo định kỳ theo yêu cầu Ban Giám đốc.",
                created_at=timezone.now() - timedelta(days=random.randint(5, 120)),
            )
            created += 1

        self.stdout.write(f"  AuditLog: {AuditLog.objects.count()}")

    # ─────────────────────────────────────────────────────────────────
    def _seed_exam_data(self, courses, actor):
        canbothi = User.objects.filter(username="cce_canbothi").first() or actor
        status_map = {
            "draft":     ExamPeriod.Status.DRAFT,
            "published": ExamPeriod.Status.PUBLISHED,
            "closed":    ExamPeriod.Status.CLOSED,
        }

        for cfg in EXAM_PERIODS_CONFIG:
            course = courses[cfg["ci"] % len(courses)]
            period, _ = ExamPeriod.objects.get_or_create(
                name=cfg["name"],
                defaults={
                    "exam_date": cfg["exam_date"],
                    "registration_deadline": cfg["reg"],
                    "cert_type": course,
                    "score_scale": Decimal("100"),
                    "max_candidates": cfg["max"],
                    "status": status_map[cfg["status"]],
                },
            )

            # Ho so hop le
            apps = []
            ps = abs(hash(cfg["name"])) % 10000
            for i in range(cfg["n"]):
                seed = ps * 1000 + i
                app, _ = ExamApplication.objects.get_or_create(
                    exam_period=period,
                    id_number=rnd_cccd(ps, i),
                    defaults={
                        "full_name": rnd_name(seed),
                        "phone": rnd_phone(seed),
                        "source": ["direct", "online", "partner"][i % 3],
                        "status": ExamApplication.Status.VALID,
                    },
                )
                apps.append(app)

            # Ho so trung lap — cung CCCD, ten khac
            for d in range(cfg.get("dup", 0)):
                if d >= len(apps):
                    break
                orig = apps[d]
                ds = ps * 999 + d + 500
                ExamApplication.objects.get_or_create(
                    exam_period=period,
                    id_number=orig.id_number,
                    full_name=rnd_name(ds),
                    defaults={
                        "phone": rnd_phone(ds),
                        "source": ["direct", "online", "partner"][d % 3],
                        "status": ExamApplication.Status.DUPLICATE,
                    },
                )

            # Phan phong thi
            valid_apps = [a for a in apps if a.status == ExamApplication.Status.VALID]
            capacity = 20
            room_map = {}
            for ri, start in enumerate(range(0, len(valid_apps), capacity)):
                chunk = valid_apps[start:start + capacity]
                room, _ = ExamRoom.objects.get_or_create(
                    exam_period=period,
                    name=ROOM_NAMES[ri % len(ROOM_NAMES)],
                    defaults={
                        "session": SESSIONS[ri % len(SESSIONS)],
                        "capacity": capacity,
                    },
                )
                asgns = []
                for app in chunk:
                    asgn, _ = ExamRoomAssignment.objects.get_or_create(
                        exam_room=room, application=app,
                    )
                    asgns.append(asgn)
                room_map[room] = asgns

            # Diem thi
            if cfg["scores"]:
                for room, asgns in room_map.items():
                    for asgn in asgns:
                        random.seed(abs(hash(str(asgn.pk))) % 99999)
                        is_absent = random.random() < 0.07
                        ExamScore.objects.get_or_create(
                            assignment=asgn,
                            defaults={
                                "score": None if is_absent else rnd_score(),
                                "is_absent": is_absent,
                                "is_published": cfg["pub"],
                                "is_locked": cfg["pub"],
                            },
                        )

            # MyBK export log
            if cfg.get("mybk") and valid_apps and canbothi:
                idx_mybk = EXAM_PERIODS_CONFIG.index(cfg)
                MyBKExportLog.objects.get_or_create(
                    exam_period=period,
                    defaults={
                        "exported_by": canbothi,
                        "record_count": len(valid_apps),
                        "notes": MYBK_NOTES[idx_mybk % len(MYBK_NOTES)],
                    },
                )

        self.stdout.write(f"  ExamPeriods: {ExamPeriod.objects.count()}")
        self.stdout.write(
            f"  ExamApplications: {ExamApplication.objects.count()} "
            f"(dup={ExamApplication.objects.filter(status='duplicate').count()})"
        )
        self.stdout.write(f"  ExamRooms: {ExamRoom.objects.count()}")
        self.stdout.write(
            f"  ExamScores: {ExamScore.objects.count()} "
            f"(vang={ExamScore.objects.filter(is_absent=True).count()})"
        )
        self.stdout.write(f"  MyBKExportLogs: {MyBKExportLog.objects.count()}")

    # ─────────────────────────────────────────────────────────────────
    def _print_summary(self):
        self.stdout.write("\n" + self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("  SEED HOAN TAT — THONG KE TOAN HE THONG"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        tc = TrainingClass.objects
        ep = ExamPeriod.objects
        ea = ExamApplication.objects
        es = ExamScore.objects
        rows = [
            ("Courses",             Course.objects.count()),
            ("Teachers",            Teacher.objects.count()),
            ("Learners",            Learner.objects.count()),
            (f"TrainingClasses (open={tc.filter(status='open').count()}, closed={tc.filter(status='closed').count()})",
             tc.count()),
            ("ClassAssignments",    ClassAssignment.objects.count()),
            ("Enrollments",         Enrollment.objects.count()),
            ("RoleFeaturePerms",    RoleFeaturePermission.objects.count()),
            ("AuditLog entries",    AuditLog.objects.count()),
            (f"ExamPeriods (draft={ep.filter(status='draft').count()}, "
             f"pub={ep.filter(status='published').count()}, "
             f"closed={ep.filter(status='closed').count()})",
             ep.count()),
            (f"ExamApplications (dup={ea.filter(status='duplicate').count()})",
             ea.count()),
            ("ExamRooms",           ExamRoom.objects.count()),
            ("ExamRoomAssignments", ExamRoomAssignment.objects.count()),
            (f"ExamScores (vang={es.filter(is_absent=True).count()}, "
             f"pub={es.filter(is_published=True).count()})",
             es.count()),
            ("MyBKExportLogs",      MyBKExportLog.objects.count()),
        ]
        for label, val in rows:
            self.stdout.write(f"  {label:<55}: {val}")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write("\n  Tai khoan demo (mat khau: CCEtest@2026!):")
        self.stdout.write("  cce_canbothi   -> Can bo thi")
        self.stdout.write("  cce_giaovu     -> Giao vu")
        self.stdout.write("  cce_giaovien   -> Giao vien")
        self.stdout.write("  cce_admin      -> Quan tri vien (superuser)\n")
