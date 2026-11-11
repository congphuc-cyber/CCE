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
