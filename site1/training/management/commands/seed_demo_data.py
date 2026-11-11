from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from training.audit import record_audit, snapshot_instance
from training.models import (
    AuditLog,
    ClassAssignment,
    Course,
    Enrollment,
    Learner,
    Teacher,
    TrainingClass,
)


COURSE_NAMES = (
    "An toàn điện cơ bản",
    "Kỹ thuật máy tính",
    "Mạng máy tính căn bản",
    "Lập trình Python ứng dụng",
    "Cơ sở dữ liệu thực hành",
    "An toàn thông tin",
    "Điện tử công nghiệp",
    "Kỹ năng tin học văn phòng",
)

FAMILY_NAMES = (
    "Nguyễn",
    "Trần",
    "Lê",
    "Phạm",
    "Hoàng",
    "Huỳnh",
    "Phan",
    "Vũ",
    "Võ",
    "Đặng",
    "Bùi",
    "Đỗ",
)

GIVEN_NAMES = (
    "Minh Anh",
    "Gia Bảo",
    "Hoàng Phúc",
    "Thảo Vy",
    "Quốc Huy",
    "Ngọc Hà",
    "Tuấn Kiệt",
    "Khánh Linh",
    "Đức Anh",
    "Phương Thảo",
)

TEACHERS = (
    ("Nguyễn Hoàng Nam", "0901000001", "nam.gv1@example.test"),
    ("Trần Thu Hà", "0901000002", "ha.gv2@example.test"),
    ("Lê Quốc Bảo", "0901000003", "bao.gv3@example.test"),
    ("Phạm Minh Tâm", "0901000004", "tam.gv4@example.test"),
    ("Võ Ngọc Lan", "0901000005", "lan.gv5@example.test"),
    ("Đặng Thanh Sơn", "0901000006", "son.gv6@example.test"),
    ("Bùi Thảo Nguyên", "0901000007", "nguyen.gv7@example.test"),
    ("Đỗ Anh Tuấn", "0901000008", "tuan.gv8@example.test"),
    ("Phan Hải Yến", "0901000009", "yen.gv9@example.test"),
    ("Huỳnh Nhật Minh", "0901000010", "minh.gv10@example.test"),
    ("Vũ Thanh Trúc", "0901000011", "truc.gv11@example.test"),
    ("Hoàng Đức Long", "0901000012", "long.gv12@example.test"),
)

SAMPLE_REASON = "Dữ liệu mẫu kiểm thử, tạo bằng lệnh seed_demo_data."


class Command(BaseCommand):
    help = "Tạo dữ liệu mẫu cho các màn đào tạo trong môi trường DEBUG."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Lệnh này chỉ được chạy khi DEBUG=True.")

        self.audit_entries_created = 0
        groups = {
            name: Group.objects.get_or_create(name=name)[0]
            for name in ("Giáo vụ", "Giáo viên")
        }
        education_user = self._role_user(groups["Giáo vụ"])
        teacher_users = list(
            get_user_model()
            .objects.filter(groups=groups["Giáo viên"], is_active=True)
            .order_by("username")
        )

        courses = self._create_courses(education_user)
        teachers = self._create_teachers(teacher_users, education_user)
        classes = self._create_classes(courses, education_user)
        learners = self._create_learners(education_user)
        assignments_created = self._assign_teachers(classes, teachers, education_user)
        enrollments_created = self._enroll_learners(classes, learners, education_user)
        self._create_sample_audit(courses, classes, learners, education_user)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready: 8 courses, 24 classes, 12 teachers, "
                f"{len(learners)} learners, {assignments_created} assignments, "
                f"{enrollments_created} enrollments, "
                f"{self.audit_entries_created} audit entries added."
            )
        )

    def _role_user(self, group):
        return (
            get_user_model()
            .objects.filter(groups=group, is_active=True)
            .order_by("username")
            .first()
        )

    def _create_courses(self, actor):
        courses = []
        for index, name in enumerate(COURSE_NAMES):
            course, created = Course.objects.get_or_create(
                name=name,
                defaults={"pass_threshold": 60 + (index % 5) * 5},
            )
            courses.append(course)
            if created:
                self._record_sample_create(actor, course)
        return courses

    def _create_teachers(self, teacher_users, actor):
        teachers = []
        for index, (name, phone, email) in enumerate(TEACHERS):
            user = teacher_users[index] if index < len(teacher_users) else None
            teacher = Teacher.objects.filter(user=user).first() if user else None
            if teacher is None:
                teacher, created = Teacher.objects.get_or_create(
                    email=email,
                    defaults={
                        "full_name": name,
                        "phone": phone,
                        "user": user,
                    },
                )
            else:
                created = False
            teachers.append(teacher)
            if created:
                self._record_sample_create(actor, teacher)
        return teachers

    def _create_classes(self, courses, actor):
        classes = []
        for index in range(24):
            course_index = index % len(courses)
            course = courses[course_index]
            status = (
                TrainingClass.Status.CLOSED
                if index % 7 == 0
                else TrainingClass.Status.OPEN
            )
            class_name = f"DEMO-{course_index + 1:02d}-{index + 1:02d}"
            training_class, created = TrainingClass.objects.get_or_create(
                name=class_name,
                defaults={
                    "course": course,
                    "learning_format": (
                        TrainingClass.LearningFormat.ONLINE
                        if index % 2
                        else TrainingClass.LearningFormat.AT_CENTER
                    ),
                    "schedule": (
                        f"Thứ {index % 6 + 2}, "
                        f"{8 + index % 9:02d}:00-{10 + index % 9:02d}:00"
                    ),
                    "max_students": 20,
                    "status": status,
                    "close_reason": SAMPLE_REASON if status == TrainingClass.Status.CLOSED else "",
                },
            )
            classes.append(training_class)
            if created:
                self._record_sample_create(actor, training_class)
        return classes

    def _create_learners(self, actor):
        learners = []
        for index in range(120):
            name = (
                f"{FAMILY_NAMES[index % len(FAMILY_NAMES)]} "
                f"{GIVEN_NAMES[(index // len(FAMILY_NAMES)) % len(GIVEN_NAMES)]} "
                f"{index + 1:03d}"
            )
            email = f"learner{index + 1:03d}@example.test"
            learner, created = Learner.objects.get_or_create(
                email=email,
                defaults={
                    "full_name": name,
                    "phone": f"091{index + 1:07d}",
                },
            )
            learners.append(learner)
            if created and index < 20:
                self._record_sample_create(actor, learner)
        return learners

    def _assign_teachers(self, classes, teachers, actor):
        created_count = 0
        for index, training_class in enumerate(classes):
            teacher_indexes = [index % len(teachers)]
            if index % 3 == 0:
                teacher_indexes.append((index + 1) % len(teachers))
            for teacher_index in teacher_indexes:
                teacher = teachers[teacher_index]
                assignment = ClassAssignment.objects.filter(
                    training_class=training_class,
                    teacher=teacher,
                    is_active=True,
                ).first()
                if assignment:
                    continue
                assignment = ClassAssignment.objects.create(
                    training_class=training_class,
                    teacher=teacher,
                )
                created_count += 1
                if index < 8:
                    self._record_sample_create(actor, assignment)
        return created_count

    def _enroll_learners(self, classes, learners, actor):
        created_count = 0
        for class_index, training_class in enumerate(classes):
            learner_count = 8 + class_index % 7
            for learner_index in range(learner_count):
                learner = learners[(class_index * 7 + learner_index * 3) % len(learners)]
                enrollment, created = Enrollment.objects.get_or_create(
                    training_class=training_class,
                    learner=learner,
                )
                if created:
                    created_count += 1
                    if class_index < 3 and learner_index < 8:
                        self._record_sample_create(actor, enrollment)
        return created_count

    def _record_sample_create(self, actor, obj):
        actor_username = actor.get_username() if actor else "Hệ thống"
        exists = AuditLog.objects.filter(
            actor_username=actor_username,
            action=AuditLog.Action.CREATE,
            object_type=obj._meta.label,
            object_id=str(obj.pk),
            reason=SAMPLE_REASON,
        ).exists()
        if not exists:
            record_audit(
                actor,
                AuditLog.Action.CREATE,
                obj,
                new_values=snapshot_instance(obj),
                reason=SAMPLE_REASON,
            )
            self.audit_entries_created += 1

    def _create_sample_audit(self, courses, classes, learners, actor):
        sample_objects = [*courses, *classes, *learners[:20]]
        for obj in sample_objects:
            self._record_sample_create(actor, obj)
