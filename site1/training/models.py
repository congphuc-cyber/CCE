from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Course(models.Model):
    name = models.CharField("Tên khóa học", max_length=150)
    pass_threshold = models.DecimalField(
        "Ngưỡng đạt (%)",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        db_table = "home_course"
        ordering = ("name",)
        verbose_name = "Khóa học"
        verbose_name_plural = "Khóa học"

    def __str__(self):
        return self.name


class Teacher(models.Model):
    full_name = models.CharField("Họ và tên", max_length=150)
    phone = models.CharField("Số điện thoại", max_length=30, blank=True)
    email = models.EmailField("Địa chỉ email", blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="Tài khoản đăng nhập",
        related_name="teacher_profile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        db_table = "home_teacher"
        ordering = ("full_name",)
        verbose_name = "Giáo viên"
        verbose_name_plural = "Giáo viên"

    def __str__(self):
        return self.full_name


class Learner(models.Model):
    full_name = models.CharField("Họ và tên", max_length=150)
    phone = models.CharField("Số điện thoại", max_length=30, blank=True)
    email = models.EmailField("Địa chỉ email", blank=True)

    class Meta:
        db_table = "home_learner"
        ordering = ("full_name",)
        verbose_name = "Học viên"
        verbose_name_plural = "Học viên"

    def __str__(self):
        return self.full_name


class TrainingClass(models.Model):
    class LearningFormat(models.TextChoices):
        AT_CENTER = "at_center", "Tại cơ sở"
        ONLINE = "online", "Online"

    class Status(models.TextChoices):
        OPEN = "open", "Đang mở"
        CLOSED = "closed", "Đã đóng"

    name = models.CharField("Tên lớp", max_length=150)
    course = models.ForeignKey(
        Course,
        verbose_name="Khóa học",
        related_name="classes",
        on_delete=models.PROTECT,
    )
    learning_format = models.CharField(
        "Hình thức học",
        max_length=20,
        choices=LearningFormat.choices,
    )
    schedule = models.CharField("Lịch học", max_length=255)
    start_date = models.DateField(
        "Ngày khai giảng",
        null=True,
        blank=True,
        help_text="Ngày dự kiến hoặc thực tế khai giảng lớp học",
    )
    max_students = models.PositiveIntegerField(
        "Sĩ số tối đa",
        validators=[MinValueValidator(1)],
    )
    status = models.CharField(
        "Trạng thái",
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    close_reason = models.TextField("Lý do đóng lớp", blank=True)
    teachers = models.ManyToManyField(
        Teacher,
        through="ClassAssignment",
        related_name="classes",
        blank=True,
    )
    learners = models.ManyToManyField(
        Learner,
        through="Enrollment",
        related_name="classes",
        blank=True,
    )

    class Meta:
        db_table = "home_trainingclass"
        ordering = ("name",)
        verbose_name = "Lớp học"
        verbose_name_plural = "Lớp học"

    def __str__(self):
        return self.name

    @property
    def enrolled_count(self):
        if not self.pk:
            return 0
        return self.enrollments.count()

    def clean(self):
        super().clean()
        if self.pk and self.max_students < self.enrolled_count:
            raise ValidationError(
                {"max_students": "Sĩ số tối đa không được nhỏ hơn số học viên đã ghi danh."}
            )
        if self.status == self.Status.CLOSED and not self.close_reason.strip():
            raise ValidationError({"close_reason": "Vui lòng nhập lý do đóng lớp."})


class ClassAssignment(models.Model):
    training_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp học",
        related_name="teacher_assignments",
        on_delete=models.CASCADE,
    )
    teacher = models.ForeignKey(
        Teacher,
        verbose_name="Giáo viên",
        related_name="teacher_assignments",
        on_delete=models.PROTECT,
    )
    assigned_at = models.DateTimeField("Thời điểm phân công", default=timezone.now)
    is_active = models.BooleanField("Đang phân công", default=True)
    removed_at = models.DateTimeField("Thời điểm bỏ phân công", null=True, blank=True)
    removal_reason = models.TextField("Lý do bỏ phân công", blank=True)

    class Meta:
        db_table = "home_classassignment"
        ordering = ("training_class__name", "teacher__full_name")
        verbose_name = "Phân công giáo viên"
        verbose_name_plural = "Phân công giáo viên"
        constraints = [
            models.UniqueConstraint(
                fields=("training_class", "teacher"),
                condition=Q(is_active=True),
                name="unique_active_class_teacher",
            )
        ]

    def __str__(self):
        return f"{self.training_class} — {self.teacher}"


class Enrollment(models.Model):
    training_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp học",
        related_name="enrollments",
        on_delete=models.PROTECT,
    )
    learner = models.ForeignKey(
        Learner,
        verbose_name="Học viên",
        related_name="enrollments",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField("Thời điểm ghi danh", auto_now_add=True)

    class Meta:
        db_table = "home_enrollment"
        ordering = ("learner__full_name",)
        verbose_name = "Ghi danh lớp học"
        verbose_name_plural = "Ghi danh lớp học"
        constraints = [
            models.UniqueConstraint(
                fields=("training_class", "learner"),
                name="unique_class_learner_enrollment",
            )
        ]

    def __str__(self):
        return f"{self.learner} — {self.training_class}"

    def clean(self):
        super().clean()
        if not self.training_class_id:
            return
        training_class = self.training_class
        if training_class.status == TrainingClass.Status.CLOSED:
            raise ValidationError({"training_class": "Lớp đã đóng, không thể ghi danh thêm."})
        existing = training_class.enrollments.exclude(pk=self.pk).count()
        if existing >= training_class.max_students:
            raise ValidationError({"training_class": "Lớp đã đạt sĩ số tối đa."})


class RoleFeaturePermission(models.Model):
    group = models.ForeignKey(
        "auth.Group",
        verbose_name="Vai trò",
        related_name="feature_permissions",
        on_delete=models.CASCADE,
    )
    feature = models.CharField("Chức năng", max_length=80)
    allowed = models.BooleanField("Được phép truy cập", default=True)

    class Meta:
        db_table = "home_rolefeaturepermission"
        ordering = ("group__name", "feature")
        verbose_name = "Quyền chức năng theo vai trò"
        verbose_name_plural = "Quyền chức năng theo vai trò"
        constraints = [
            models.UniqueConstraint(
                fields=("group", "feature"),
                name="unique_role_feature_permission",
            )
        ]

    def __str__(self):
        state = "Được phép" if self.allowed else "Không được phép"
        return f"{self.group.name} — {self.feature}: {state}"


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Tạo"
        EDIT = "edit", "Sửa"
        DELETE = "delete", "Xóa"
        PUBLISH = "publish", "Công bố"
        ISSUE = "issue", "Cấp"
        REVOKE = "revoke", "Thu hồi"
        EXPORT = "export", "Xuất dữ liệu"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người thực hiện",
        related_name="audit_logs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    actor_username = models.CharField("Tên đăng nhập người thực hiện", max_length=150)
    action = models.CharField("Loại thao tác", max_length=20, choices=Action.choices)
    object_type = models.CharField("Loại đối tượng", max_length=100)
    object_id = models.CharField("Mã đối tượng", max_length=100, blank=True)
    object_label = models.CharField("Đối tượng", max_length=255)
    old_values = models.JSONField("Giá trị cũ", default=dict, blank=True)
    new_values = models.JSONField("Giá trị mới", default=dict, blank=True)
    reason = models.TextField("Lý do", blank=True)
    created_at = models.DateTimeField("Thời điểm", default=timezone.now, editable=False)

    class Meta:
        db_table = "home_auditlog"
        ordering = ("-created_at", "-pk")
        verbose_name = "Nhật ký thao tác"
        verbose_name_plural = "Nhật ký thao tác"
        indexes = [
            models.Index(fields=("action", "created_at"), name="audit_action_time_idx"),
            models.Index(fields=("actor", "created_at"), name="audit_actor_time_idx"),
        ]

    def __str__(self):
        return f"{self.get_action_display()} — {self.object_label}"


class ExamPeriod(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Chưa công bố"
        PUBLISHED = "published", "Đã công bố"
        CLOSED = "closed", "Đã đóng"

    name = models.CharField("Tên đợt thi", max_length=200)
    cert_type = models.ForeignKey(
        "Course", verbose_name="Loại chứng chỉ",
        related_name="exam_periods", on_delete=models.PROTECT,
    )
    exam_date = models.DateField("Ngày thi")
    registration_deadline = models.DateField("Hạn đăng ký")
    max_candidates = models.PositiveIntegerField("Số thí sinh tối đa", validators=[MinValueValidator(1)])
    score_scale = models.DecimalField("Thang điểm", max_digits=5, decimal_places=2, default=100)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField("Ngày tạo", auto_now_add=True)

    class Meta:
        db_table = "home_examperiod"
        ordering = ("-exam_date", "name")
        verbose_name = "Đợt thi"
        verbose_name_plural = "Đợt thi"

    def __str__(self): return self.name

    def clean(self):
        super().clean()
        if self.registration_deadline and self.exam_date:
            if self.registration_deadline >= self.exam_date:
                raise ValidationError({"registration_deadline": "Hạn đăng ký phải trước ngày thi."})
        if self.pk and self.status == self.Status.DRAFT and self.applications.exists():
            raise ValidationError({"status": "Không thể chuyển đợt thi về 'Chưa công bố' khi đã có hồ sơ đăng ký."})
        if self.pk and self.max_candidates:
            curr_apps = self.applications.count()
            if self.max_candidates < curr_apps:
                raise ValidationError({"max_candidates": f"Số hồ sơ tối đa ({self.max_candidates}) không được nhỏ hơn số hồ sơ hiện có ({curr_apps})."})
        if self.pk:
            orig = ExamPeriod.objects.filter(pk=self.pk).first()
            if orig and orig.status == self.Status.CLOSED:
                changed = any(
                    getattr(self, f) != getattr(orig, f)
                    for f in ["name", "cert_type_id", "exam_date", "registration_deadline", "max_candidates", "score_scale", "status"]
                )
                if changed:
                    raise ValidationError("Đợt thi đã đóng, không thể chỉnh sửa thông tin.")

    @property
    def application_count(self):
        if self.status == self.Status.DRAFT:
            return 0
        return self.applications.count() if self.pk else 0


class ExamApplication(models.Model):
    class Source(models.TextChoices):
        ONLINE = "online", "Trực tuyến"
        GOOGLE_FORM = "google_form", "Google Form"
        PARTNER = "partner", "Đối tác"
        DIRECT = "direct", "Tại cơ sở"

    class Status(models.TextChoices):
        NEW = "new", "Mới"
        VALID = "valid", "Hợp lệ"
        DUPLICATE = "duplicate", "Trùng"

    exam_period = models.ForeignKey(
        ExamPeriod, verbose_name="Đợt thi",
        related_name="applications", on_delete=models.PROTECT,
    )
    full_name = models.CharField("Họ và tên", max_length=150)
    phone = models.CharField("Số điện thoại", max_length=30, blank=True)
    id_number = models.CharField("Số CCCD / CMND", max_length=20)
    source = models.CharField("Nguồn", max_length=20, choices=Source.choices, default=Source.ONLINE)
    registered_at = models.DateTimeField("Ngày đăng ký", auto_now_add=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.NEW)

    class Meta:
        db_table = "home_examapplication"
        ordering = ("-registered_at",)
        verbose_name = "Hồ sơ đăng ký dự thi"
        verbose_name_plural = "Hồ sơ đăng ký dự thi"

    def __str__(self):
        return f"{self.full_name} — {self.exam_period}"

    def clean(self):
        super().clean()
        if self.exam_period_id and self.exam_period.status == ExamPeriod.Status.DRAFT:
            raise ValidationError(
                {"exam_period": "Đợt thi chưa công bố không thể tiếp nhận hồ sơ đăng ký."}
            )
        if self.exam_period_id and self.exam_period.status == ExamPeriod.Status.CLOSED:
            raise ValidationError(
                {"exam_period": "Đợt thi đã đóng, không thể tiếp nhận thêm hồ sơ đăng ký."}
            )
        if self.exam_period_id and self.exam_period.max_candidates:
            existing = self.exam_period.applications.exclude(pk=self.pk).count()
            if existing >= self.exam_period.max_candidates:
                raise ValidationError(
                    {"exam_period": f"Đợt thi đã đạt số lượng hồ sơ tối đa ({self.exam_period.max_candidates})."}
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ExamRoom(models.Model):
    exam_period = models.ForeignKey(
        ExamPeriod, verbose_name="Đợt thi",
        related_name="rooms", on_delete=models.PROTECT,
    )
    name = models.CharField("Tên phòng", max_length=100)
    session = models.CharField("Ca thi", max_length=100)
    capacity = models.PositiveIntegerField("Sức chứa", validators=[MinValueValidator(1)])

    class Meta:
        db_table = "home_examroom"
        ordering = ("exam_period", "name")
        verbose_name = "Phòng thi"
        verbose_name_plural = "Phòng thi"

    def __str__(self):
        return f"{self.name} — {self.exam_period}"

    @property
    def assigned_count(self):
        return self.room_assignments.count() if self.pk else 0

    @property
    def remaining_capacity(self):
        return max(0, self.capacity - self.assigned_count)


class ExamRoomAssignment(models.Model):
    exam_room = models.ForeignKey(
        ExamRoom, verbose_name="Phòng thi",
        related_name="room_assignments", on_delete=models.CASCADE,
    )
    application = models.ForeignKey(
        ExamApplication, verbose_name="Hồ sơ đăng ký",
        related_name="room_assignments", on_delete=models.PROTECT,
    )
    assigned_at = models.DateTimeField("Thời điểm xếp phòng", auto_now_add=True)

    class Meta:
        db_table = "home_examroomassignment"
        ordering = ("exam_room", "application__full_name")
        verbose_name = "Xếp phòng thi"
        verbose_name_plural = "Xếp phòng thi"
        constraints = [
            models.UniqueConstraint(fields=("exam_room", "application"), name="unique_room_application"),
        ]

    def __str__(self):
        return f"{self.application.full_name} → {self.exam_room.name}"

    def clean(self):
        super().clean()
        if self.application_id and self.application.status != ExamApplication.Status.VALID:
            raise ValidationError({"application": "Chỉ hồ sơ Hợp lệ mới được xếp phòng thi."})
        if self.exam_room_id:
            current = self.exam_room.room_assignments.exclude(pk=self.pk).count()
            if current >= self.exam_room.capacity:
                raise ValidationError({"exam_room": "Phòng thi đã đủ sức chứa."})


class ExamScore(models.Model):
    assignment = models.OneToOneField(
        ExamRoomAssignment, verbose_name="Xếp phòng",
        related_name="score", on_delete=models.PROTECT,
    )
    score = models.DecimalField("Điểm", max_digits=5, decimal_places=2, null=True, blank=True)
    is_absent = models.BooleanField("Vắng thi", default=False)
    is_published = models.BooleanField("Đã công bố", default=False)
    is_locked = models.BooleanField("Đã chốt", default=False)
    entered_at = models.DateTimeField("Thời điểm nhập điểm", null=True, blank=True)
    published_at = models.DateTimeField("Thời điểm công bố", null=True, blank=True)

    class Meta:
        db_table = "home_examscore"
        verbose_name = "Điểm thi"
        verbose_name_plural = "Điểm thi"

    @property
    def has_result(self):
        return self.score is not None or self.is_absent

    @property
    def blocks_room_removal(self):
        return self.has_result or self.is_published

    def __str__(self):
        return f"Điểm {self.assignment}"

    def clean(self):
        super().clean()
        if self.score is not None:
            scale = self.assignment.exam_room.exam_period.score_scale if self.assignment_id else None
            if scale is not None and self.score > scale:
                raise ValidationError({"score": f"Điểm không được vượt thang điểm {scale}."})
            if self.score < 0:
                raise ValidationError({"score": "Điểm không được âm."})


class MyBKExportLog(models.Model):
    exam_period = models.ForeignKey(
        ExamPeriod, verbose_name="Đợt thi",
        related_name="mybk_exports", on_delete=models.PROTECT,
    )
    exported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Người thực hiện",
        null=True, blank=True, on_delete=models.SET_NULL,
    )
    exported_at = models.DateTimeField("Thời điểm xuất", auto_now_add=True)
    record_count = models.PositiveIntegerField("Số bản ghi", default=0)
    notes = models.TextField("Ghi chú", blank=True)

    class Meta:
        db_table = "home_mybkexportlog"
        ordering = ("-exported_at",)
        verbose_name = "Nhật ký xuất MyBK"
        verbose_name_plural = "Nhật ký xuất MyBK"

    def __str__(self):
        return f"Xuất MyBK {self.exam_period} — {self.exported_at:%d/%m/%Y}"
