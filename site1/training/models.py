from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
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
    date_of_birth = models.DateField("Ngày sinh", null=True, blank=True)
    id_number = models.CharField(
        "Số CCCD",
        max_length=12,
        null=True,
        blank=True,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^[0-9]{12}$",
                message="Số CCCD phải gồm đúng 12 chữ số.",
            )
        ],
    )
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
    final_results_locked_at = models.DateTimeField(
        "Thời điểm chốt kết quả cuối khóa",
        null=True,
        blank=True,
    )
    final_results_locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người chốt kết quả cuối khóa",
        related_name="locked_final_result_classes",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
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

    @property
    def final_results_locked(self):
        return self.final_results_locked_at is not None

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


class EnrollmentTransferHistory(models.Model):
    learner = models.ForeignKey(
        Learner,
        verbose_name="Học viên",
        related_name="transfer_history",
        on_delete=models.PROTECT,
    )
    source_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp cũ",
        related_name="transfers_out",
        on_delete=models.PROTECT,
    )
    destination_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp mới",
        related_name="transfers_in",
        on_delete=models.PROTECT,
    )
    transferred_at = models.DateTimeField("Thời điểm chuyển", default=timezone.now, editable=False)
    transferred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người thực hiện",
        related_name="enrollment_transfers",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        db_table = "home_enrollmenttransferhistory"
        ordering = ("-transferred_at", "-pk")
        verbose_name = "Lịch sử chuyển lớp"
        verbose_name_plural = "Lịch sử chuyển lớp"
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_class=F("destination_class")),
                name="distinct_transfer_classes",
            )
        ]

    def __str__(self):
        return f"{self.learner}: {self.source_class} → {self.destination_class}"


class FinalCourseResult(models.Model):
    training_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp học",
        related_name="final_results",
        on_delete=models.PROTECT,
    )
    learner = models.ForeignKey(
        Learner,
        verbose_name="Học viên",
        related_name="final_course_results",
        on_delete=models.PROTECT,
    )
    score = models.DecimalField(
        "Điểm cuối khóa",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người cập nhật",
        related_name="modified_final_course_results",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField("Thời điểm tạo", auto_now_add=True)
    updated_at = models.DateTimeField("Thời điểm cập nhật", auto_now=True)

    class Meta:
        db_table = "home_finalcourseresult"
        ordering = ("training_class__name", "learner__full_name")
        verbose_name = "Kết quả cuối khóa"
        verbose_name_plural = "Kết quả cuối khóa"
        constraints = [
            models.UniqueConstraint(
                fields=("training_class", "learner"),
                name="unique_final_course_result",
            )
        ]

    def __str__(self):
        return f"{self.learner} — {self.training_class}: {self.score}"

    @property
    def outcome(self):
        return (
            "Đạt"
            if self.score >= self.training_class.course.pass_threshold
            else "Không đạt"
        )


class Certificate(models.Model):
    class Status(models.TextChoices):
        ISSUED = "issued", "Đã cấp"
        REVOKED = "revoked", "Thu hồi"
        REPLACED = "replaced", "Đã thay thế"

    training_class = models.ForeignKey(
        TrainingClass,
        verbose_name="Lớp học",
        related_name="certificates",
        on_delete=models.PROTECT,
    )
    learner = models.ForeignKey(
        Learner,
        verbose_name="Học viên",
        related_name="certificates",
        on_delete=models.PROTECT,
    )
    certificate_number = models.CharField("Số chứng nhận", max_length=50, unique=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.ISSUED)
    issued_at = models.DateTimeField("Thời điểm cấp", default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người cấp",
        related_name="issued_certificates",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reason = models.TextField("Lý do", blank=True)
    replaced_by = models.ForeignKey(
        "self",
        verbose_name="Thay thế bởi",
        related_name="replaced_certificate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    replaced_at = models.DateTimeField("Thời điểm thay thế", null=True, blank=True)

    class Meta:
        db_table = "home_certificate"
        ordering = ("-issued_at", "-pk")
        verbose_name = "Chứng nhận"
        verbose_name_plural = "Chứng nhận"

    def __str__(self):
        return f"{self.certificate_number} — {self.learner}"

    @property
    def is_active(self):
        return self.status == self.Status.ISSUED

    def clean(self):
        super().clean()
        if self.training_class_id and self.learner_id:
            final_result = FinalCourseResult.objects.filter(
                training_class=self.training_class,
                learner=self.learner,
            ).first()
            if final_result is None:
                raise ValidationError({"learner": "Học viên chưa có kết quả cuối khóa trong lớp này."})
            if final_result.score < self.training_class.course.pass_threshold:
                raise ValidationError({"learner": "Học viên chưa đạt điều kiện cấp chứng nhận."})
            active_qs = Certificate.objects.filter(
                training_class=self.training_class,
                learner=self.learner,
            ).exclude(pk=self.pk)
            if self.status == self.Status.ISSUED and active_qs.filter(status=self.Status.ISSUED).exists():
                raise ValidationError({"status": "Học viên này đã có chứng nhận còn hiệu lực trong lớp."})


class CertificateHistory(models.Model):
    certificate = models.ForeignKey(
        Certificate,
        verbose_name="Chứng nhận",
        related_name="history",
        on_delete=models.CASCADE,
    )
    old_status = models.CharField("Trạng thái cũ", max_length=20, choices=Certificate.Status.choices)
    new_status = models.CharField("Trạng thái mới", max_length=20, choices=Certificate.Status.choices)
    reason = models.TextField("Lý do", blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người thay đổi",
        related_name="certificate_history",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    changed_at = models.DateTimeField("Thời điểm thay đổi", default=timezone.now)

    class Meta:
        db_table = "home_certificatehistory"
        ordering = ("-changed_at", "-pk")
        verbose_name = "Lịch sử thay đổi chứng nhận"
        verbose_name_plural = "Lịch sử thay đổi chứng nhận"

    def __str__(self):
        return f"{self.certificate_number} — {self.old_status} → {self.new_status}"

    @property
    def certificate_number(self):
        return self.certificate.certificate_number


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
        REPLACE = "replace", "Thay thế"
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
    date_of_birth = models.DateField("Ngày sinh", null=True, blank=True)
    phone = models.CharField("Số điện thoại", max_length=30, blank=True)
    id_number = models.CharField("Số CCCD / CMND", max_length=20)
    email = models.EmailField("Địa chỉ email", blank=True)
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


class ExamCertificate(models.Model):
    class Status(models.TextChoices):
        ISSUED = "issued", "Đã cấp"
        REVOKED = "revoked", "Thu hồi"
        REPLACED = "replaced", "Đã thay thế"

    exam_period = models.ForeignKey(
        ExamPeriod, verbose_name="Đợt thi",
        related_name="certificates", on_delete=models.PROTECT,
    )
    application = models.ForeignKey(
        ExamApplication, verbose_name="Thí sinh",
        related_name="certificates", on_delete=models.PROTECT,
    )
    certificate_number = models.CharField("Số chứng chỉ", max_length=50, unique=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.ISSUED)
    issued_at = models.DateTimeField("Thời điểm cấp", default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người cấp",
        related_name="issued_exam_certificates",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reason = models.TextField("Lý do", blank=True)
    replaced_by = models.ForeignKey(
        "self",
        verbose_name="Thay thế bởi",
        related_name="replaced_certificate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    replaced_at = models.DateTimeField("Thời điểm thay thế", null=True, blank=True)

    class Meta:
        db_table = "home_examcertificate"
        ordering = ("-issued_at", "-pk")
        verbose_name = "Chứng chỉ khảo thí"
        verbose_name_plural = "Chứng chỉ khảo thí"

    def __str__(self):
        return f"{self.certificate_number} — {self.application.full_name}"

    @property
    def is_active(self):
        return self.status == self.Status.ISSUED

    def clean(self):
        super().clean()
        if self.exam_period_id and self.application_id:
            score = ExamScore.objects.filter(
                assignment__application=self.application,
                assignment__exam_room__exam_period=self.exam_period,
            ).select_related("assignment__exam_room__exam_period").first()
            if not score:
                raise ValidationError({"application": "Thí sinh chưa có điểm trong đợt thi này."})
            if score.is_absent or score.score is None or score.score < 5:
                raise ValidationError({"application": "Thí sinh chưa đạt hoặc điểm chưa được công bố."})
            if not score.is_published:
                raise ValidationError({"application": "Điểm phải được công bố trước khi cấp chứng chỉ."})
            if (
                ExamCertificate.objects.filter(
                    exam_period=self.exam_period,
                    application=self.application,
                )
                .exclude(pk=self.pk)
                .filter(status=ExamCertificate.Status.ISSUED)
                .exists()
            ):
                raise ValidationError({"status": "Thí sinh này đã có chứng chỉ còn hiệu lực trong đợt thi."})


class ExamCertificateHistory(models.Model):
    certificate = models.ForeignKey(
        ExamCertificate,
        verbose_name="Chứng chỉ",
        related_name="history",
        on_delete=models.CASCADE,
    )
    old_status = models.CharField("Trạng thái cũ", max_length=20, choices=ExamCertificate.Status.choices)
    new_status = models.CharField("Trạng thái mới", max_length=20, choices=ExamCertificate.Status.choices)
    reason = models.TextField("Lý do", blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Người thay đổi",
        related_name="exam_certificate_history",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    changed_at = models.DateTimeField("Thời điểm thay đổi", default=timezone.now)

    class Meta:
        db_table = "home_examcertificatehistory"
        ordering = ("-changed_at", "-pk")
        verbose_name = "Lịch sử thay đổi chứng chỉ"
        verbose_name_plural = "Lịch sử thay đổi chứng chỉ"

    def __str__(self):
        return f"{self.certificate.certificate_number} — {self.old_status} → {self.new_status}"


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
