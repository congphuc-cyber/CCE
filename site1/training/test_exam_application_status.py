from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from training.exam_views import (
    ExamApplicationFilterForm,
    _mark_existing_exam_application_duplicates,
)
from training.models import Course, ExamApplication, ExamPeriod


class ExamApplicationStatusTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            name="Chứng chỉ kiểm tra",
            pass_threshold=70,
        )
        self.period = ExamPeriod.objects.create(
            name="Đợt thi trực tuyến",
            cert_type=self.course,
            exam_date=timezone.localdate() + timedelta(days=6),
            registration_deadline=timezone.localdate() + timedelta(days=5),
            max_candidates=1,
            status=ExamPeriod.Status.PUBLISHED,
        )

    def valid_payload(self, id_number="123456789012"):
        return {
            "cert_type": str(self.course.pk),
            "exam_period": str(self.period.pk),
            "full_name": "Nguyễn Văn Dự Thi",
            "date_of_birth": "2000-01-02",
            "id_number": id_number,
            "phone": "0901234567",
            "email": "candidate@example.com",
        }

    def test_application_statuses_are_limited_to_valid_and_duplicate(self):
        self.assertEqual(
            ExamApplication.Status.values,
            ["valid", "duplicate"],
        )
        form = ExamApplicationFilterForm()
        self.assertEqual(
            list(form.fields["status"].choices),
            [
                ("", "Tất cả trạng thái"),
                ("valid", "Hợp lệ"),
                ("duplicate", "Trùng"),
            ],
        )

    def test_public_registration_creates_valid_application(self):
        response = self.client.post(
            reverse("exam-application-public-register"),
            self.valid_payload(),
        )

        self.assertRedirects(
            response,
            reverse("exam-application-public-success"),
        )
        application = ExamApplication.objects.get()
        self.assertEqual(application.id_number, "123456789012")
        self.assertEqual(application.status, ExamApplication.Status.VALID)

    def test_public_registration_requires_exactly_twelve_identity_digits(self):
        response = self.client.post(
            reverse("exam-application-public-register"),
            self.valid_payload(id_number="123456789"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Số CCCD / CMND phải gồm đúng 12 chữ số.",
            status_code=400,
        )
        self.assertFalse(ExamApplication.objects.exists())

    def test_public_registration_marks_repeated_identity_as_duplicate(self):
        existing = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Hồ sơ đăng ký trước",
            id_number="123456789012",
        )

        response = self.client.post(
            reverse("exam-application-public-register"),
            self.valid_payload(),
        )

        self.assertRedirects(
            response,
            reverse("exam-application-public-success"),
        )
        applications = ExamApplication.objects.filter(
            exam_period=self.period,
            id_number="123456789012",
        )
        self.assertEqual(applications.count(), 2)
        self.assertEqual(
            set(applications.values_list("status", flat=True)),
            {ExamApplication.Status.DUPLICATE},
        )
        new_application = applications.exclude(pk=existing.pk).get()
        self.assertEqual(new_application.status, ExamApplication.Status.DUPLICATE)
        existing.refresh_from_db()
        self.assertEqual(existing.status, ExamApplication.Status.DUPLICATE)

    def test_same_identity_in_a_different_exam_period_is_valid(self):
        other_period = ExamPeriod.objects.create(
            name="Đợt thi khác",
            cert_type=self.course,
            exam_date=timezone.localdate() + timedelta(days=8),
            registration_deadline=timezone.localdate() + timedelta(days=7),
            max_candidates=1,
            status=ExamPeriod.Status.PUBLISHED,
        )
        ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Hồ sơ đợt trước",
            id_number="123456789012",
        )
        payload = self.valid_payload()
        payload["exam_period"] = str(other_period.pk)

        response = self.client.post(
            reverse("exam-application-public-register"),
            payload,
        )

        self.assertRedirects(
            response,
            reverse("exam-application-public-success"),
        )
        application = ExamApplication.objects.get(exam_period=other_period)
        self.assertEqual(application.status, ExamApplication.Status.VALID)

    def test_existing_duplicate_groups_mark_all_matching_applications_duplicate(self):
        applications = ExamApplication.objects.bulk_create(
            [
                ExamApplication(
                    exam_period=self.period,
                    full_name="Hồ sơ đăng ký trước",
                    id_number="123456789012",
                    status=ExamApplication.Status.VALID,
                ),
                ExamApplication(
                    exam_period=self.period,
                    full_name="Hồ sơ đăng ký sau",
                    id_number="123456789012",
                    status=ExamApplication.Status.VALID,
                ),
            ]
        )

        _mark_existing_exam_application_duplicates()

        applications[0].refresh_from_db()
        applications[1].refresh_from_db()
        self.assertEqual(applications[0].status, ExamApplication.Status.DUPLICATE)
        self.assertEqual(applications[1].status, ExamApplication.Status.DUPLICATE)
