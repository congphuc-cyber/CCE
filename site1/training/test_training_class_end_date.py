from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from training.forms import TrainingClassAdminForm
from training.models import Course, TrainingClass


class TrainingClassEndDateTests(TestCase):
    def setUp(self):
        education_group = Group.objects.create(name="Giáo vụ")
        user = User.objects.create_user(
            username="class-end-date-manager",
            password="test-password",
        )
        user.groups.add(education_group)
        self.client.force_login(user)

        self.course = Course.objects.create(
            name="Lập trình Python ứng dụng",
            pass_threshold=75,
        )

    def test_tenth_session_date_counts_selected_weekdays_from_start_date(self):
        end_date = TrainingClassAdminForm.calculate_tenth_session_date(
            date(2026, 9, 14),
            "Thứ 2, Thứ 4, Thứ 6 — Ca 5 — Tối (18:00–20:00)",
        )

        self.assertEqual(end_date, date(2026, 10, 5))

    def test_tenth_session_starts_on_next_scheduled_day_if_start_is_not_scheduled(self):
        end_date = TrainingClassAdminForm.calculate_tenth_session_date(
            date(2026, 9, 15),
            "Thứ 2, Thứ 4, Thứ 6 — Ca 5 — Tối (18:00–20:00)",
        )

        self.assertEqual(end_date, date(2026, 10, 7))

    def test_end_date_is_unavailable_without_start_date_or_recognized_schedule(self):
        self.assertIsNone(
            TrainingClassAdminForm.calculate_tenth_session_date(
                None,
                "Thứ 2, Thứ 4 — Ca 5 — Tối (18:00–20:00)",
            )
        )
        self.assertIsNone(
            TrainingClassAdminForm.calculate_tenth_session_date(
                date(2026, 9, 14),
                "",
            )
        )

    def test_admin_list_shows_computed_end_date_and_missing_date(self):
        TrainingClass.objects.create(
            name="CCE-LAPTR-901",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ 2, Thứ 4, Thứ 6 — Ca 5 — Tối (18:00–20:00)",
            start_date=date(2026, 9, 14),
            max_students=10,
        )
        TrainingClass.objects.create(
            name="Lớp chưa có ngày khai giảng",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ 2, Thứ 4, Thứ 6 — Ca 5 — Tối (18:00–20:00)",
            max_students=10,
        )

        response = self.client.get(
            reverse("admin:training_trainingclass_changelist")
        )

        self.assertContains(response, "Ngày khai giảng")
        self.assertContains(response, "Ngày kết thúc")
        self.assertContains(response, "14/09/2026")
        self.assertContains(response, "05/10/2026")
        self.assertContains(response, "Chưa có thông tin")
