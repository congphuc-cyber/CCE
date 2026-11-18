from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from training.models import (
    AuditLog,
    Course,
    Enrollment,
    FinalCourseResult,
    Learner,
    TrainingClass,
)


class EnrollmentCancellationTests(TestCase):
    def setUp(self):
        education_group = Group.objects.create(name="Giáo vụ")
        self.user = User.objects.create_user(
            username="enrollment-manager",
            password="test-password",
        )
        self.user.groups.add(education_group)
        course = Course.objects.create(name="An toàn điện", pass_threshold=75)
        self.training_class = TrainingClass.objects.create(
            name="ATD-01",
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai, 18:00",
            max_students=10,
        )
        self.client.force_login(self.user)

    def create_enrollment(self, name):
        learner = Learner.objects.create(full_name=name)
        enrollment = Enrollment.objects.create(
            training_class=self.training_class,
            learner=learner,
        )
        return learner, enrollment

    def test_class_detail_shows_cancel_action_and_reason_popup_for_final_results(self):
        _, cancellable_enrollment = self.create_enrollment("Học viên chưa có kết quả")
        absent_learner, _ = self.create_enrollment("Học viên đã vắng")
        FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=absent_learner,
            is_absent=True,
        )
        scored_learner, _ = self.create_enrollment("Học viên đã có điểm")
        FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=scored_learner,
            score=Decimal("80"),
        )

        response = self.client.get(
            reverse("class-detail", args=(self.training_class.pk,))
        )

        self.assertContains(response, 'class="cce-table cce-table-centered"')
        self.assertContains(response, "Hủy ghi danh")
        self.assertContains(response, "Không thể hủy ghi danh")
        self.assertContains(response, "enrollment-cancel-blocked-modal")
        self.assertContains(response, "Học viên đã được ghi nhận vắng")
        self.assertContains(response, "Học viên đã có điểm cuối khóa")
        self.assertNotContains(response, "onsubmit=\"return confirm(")
        self.assertContains(
            response,
            reverse(
                "class-enrollment-cancel",
                args=(self.training_class.pk, cancellable_enrollment.pk),
            ),
        )

    def test_cancel_removes_enrollment_and_records_audit(self):
        learner, enrollment = self.create_enrollment("Học viên cần hủy")

        response = self.client.post(
            reverse(
                "class-enrollment-cancel",
                args=(self.training_class.pk, enrollment.pk),
            )
        )

        self.assertRedirects(
            response,
            reverse("class-detail", args=(self.training_class.pk,)),
        )
        self.assertFalse(Enrollment.objects.filter(pk=enrollment.pk).exists())
        audit = AuditLog.objects.get(
            action=AuditLog.Action.DELETE,
            object_type="training.Enrollment",
            object_id=str(enrollment.pk),
        )
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.old_values["learner"], learner.pk)

    def test_cancel_is_rejected_if_learner_has_score_or_is_absent(self):
        cases = (
            ("Học viên đã có điểm", Decimal("80"), False),
            ("Học viên vắng", None, True),
        )
        for name, score, is_absent in cases:
            with self.subTest(name=name):
                learner, enrollment = self.create_enrollment(name)
                FinalCourseResult.objects.create(
                    training_class=self.training_class,
                    learner=learner,
                    score=score,
                    is_absent=is_absent,
                )

                response = self.client.post(
                    reverse(
                        "class-enrollment-cancel",
                        args=(self.training_class.pk, enrollment.pk),
                    ),
                    follow=True,
                )

                self.assertTrue(Enrollment.objects.filter(pk=enrollment.pk).exists())
                self.assertContains(
                    response,
                    "Không thể hủy ghi danh vì học viên đã có điểm cuối khóa hoặc được ghi nhận vắng.",
                )

    def test_closed_class_prevents_cancellation_with_or_without_final_results(self):
        cases = (
            ("Học viên chưa có kết quả", None, False),
            ("Học viên đã có điểm", Decimal("80"), False),
            ("Học viên vắng", None, True),
        )
        self.training_class.status = TrainingClass.Status.CLOSED
        self.training_class.save(update_fields=("status",))

        self.create_enrollment("Học viên chưa có kết quả trên trang")
        page_response = self.client.get(
            reverse("class-detail", args=(self.training_class.pk,))
        )
        self.assertContains(page_response, "Lớp đã đóng nên không thể hủy ghi danh.")
        self.assertNotContains(page_response, "Xác nhận hủy ghi danh")

        for name, score, is_absent in cases:
            with self.subTest(name=name):
                learner, enrollment = self.create_enrollment(name)
                if score is not None or is_absent:
                    FinalCourseResult.objects.create(
                        training_class=self.training_class,
                        learner=learner,
                        score=score,
                        is_absent=is_absent,
                    )

                response = self.client.post(
                    reverse(
                        "class-enrollment-cancel",
                        args=(self.training_class.pk, enrollment.pk),
                    ),
                    follow=True,
                )

                self.assertTrue(Enrollment.objects.filter(pk=enrollment.pk).exists())
                self.assertContains(response, "Lớp đã đóng nên không thể hủy ghi danh.")
