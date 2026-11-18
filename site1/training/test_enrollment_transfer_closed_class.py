from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from training.models import (
    Course,
    Enrollment,
    EnrollmentTransferHistory,
    Learner,
    TrainingClass,
)


class ClosedClassTransferTests(TestCase):
    def setUp(self):
        education_group = Group.objects.create(name="Giáo vụ")
        user = User.objects.create_user(
            username="transfer-manager",
            password="test-password",
        )
        user.groups.add(education_group)
        self.client.force_login(user)

        course = Course.objects.create(name="An toàn điện", pass_threshold=75)
        self.source_class = self.create_class("Lớp nguồn", course)
        self.destination_class = self.create_class("Lớp đích", course)
        self.learner = Learner.objects.create(full_name="Nguyễn An")
        Enrollment.objects.create(
            learner=self.learner,
            training_class=self.source_class,
        )

    def create_class(self, name, course):
        return TrainingClass.objects.create(
            name=name,
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai",
            max_students=10,
        )

    def submit_transfer(self):
        return self.client.post(
            reverse("class-transfer"),
            {
                "learner": self.learner.pk,
                "source_class": self.source_class.pk,
                "destination_class": self.destination_class.pk,
            },
        )

    def assert_transfer_was_not_performed(self, response, error):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, error)
        self.assertContains(response, 'id="class-transfer-closed-modal"')
        self.assertTrue(
            Enrollment.objects.filter(
                learner=self.learner,
                training_class=self.source_class,
            ).exists()
        )
        self.assertFalse(
            Enrollment.objects.filter(
                learner=self.learner,
                training_class=self.destination_class,
            ).exists()
        )
        self.assertFalse(EnrollmentTransferHistory.objects.exists())

    def test_closed_source_class_shows_popup_and_rejects_transfer(self):
        self.source_class.status = TrainingClass.Status.CLOSED
        self.source_class.close_reason = "Đã kết thúc"
        self.source_class.save(update_fields=("status", "close_reason"))

        response = self.submit_transfer()

        self.assert_transfer_was_not_performed(
            response,
            "Lớp hiện tại đã đóng, không thể chuyển học viên.",
        )
        self.assertContains(response, "closed-source-class-ids")
        self.assertContains(response, "openClosedModal(sourceSelect, true)")

    def test_closed_destination_class_shows_popup_and_rejects_transfer(self):
        self.destination_class.status = TrainingClass.Status.CLOSED
        self.destination_class.close_reason = "Đã kết thúc"
        self.destination_class.save(update_fields=("status", "close_reason"))

        response = self.submit_transfer()

        self.assert_transfer_was_not_performed(
            response,
            "Lớp mới đã đóng, không thể nhận học viên.",
        )
        self.assertContains(response, "closed-destination-class-ids")
        self.assertContains(response, "openClosedModal(destinationSelect, false)")
