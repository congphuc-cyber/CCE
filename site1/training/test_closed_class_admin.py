from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from training.models import (
    ClassAssignment,
    Course,
    Enrollment,
    Learner,
    Teacher,
    TrainingClass,
)


class ClosedClassAdminTests(TestCase):
    def setUp(self):
        education_group = Group.objects.create(name="Giáo vụ")
        user = User.objects.create_user(
            username="class-admin-manager",
            password="test-password",
        )
        user.groups.add(education_group)
        self.client.force_login(user)

        course = Course.objects.create(name="An toàn điện", pass_threshold=75)
        self.open_class = TrainingClass.objects.create(
            name="Lớp đang mở",
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai",
            max_students=10,
        )
        self.closed_class = TrainingClass.objects.create(
            name="Lớp đã đóng",
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Ba",
            max_students=10,
            status=TrainingClass.Status.CLOSED,
            close_reason="Đã kết thúc",
        )

    def test_closed_class_edit_action_opens_popup_but_open_class_keeps_edit_link(self):
        response = self.client.get(
            reverse("admin:training_trainingclass_changelist")
        )

        self.assertContains(response, 'data-bs-target="#closed-class-edit-modal"')
        self.assertContains(response, "Lớp đã đóng nên không thể chỉnh sửa thông tin.")
        self.assertContains(
            response,
            reverse(
                "admin:training_trainingclass_change",
                args=(self.open_class.pk,),
            ),
        )

    def test_direct_closed_class_edit_requests_redirect_to_popup(self):
        change_url = reverse(
            "admin:training_trainingclass_change",
            args=(self.closed_class.pk,),
        )
        changelist_url = reverse("admin:training_trainingclass_changelist")

        for method, data in (
            (self.client.get, None),
            (self.client.post, {"name": "Tên không được lưu"}),
        ):
            with self.subTest(method=method.__name__):
                response = method(change_url, data, follow=True)
                self.assertRedirects(
                    response,
                    changelist_url,
                )
                self.assertContains(
                    response,
                    'class="cce-modal-backdrop is-open"',
                )
                self.assertContains(
                    response,
                    "Lớp đã đóng nên không thể chỉnh sửa thông tin.",
                )

        self.closed_class.refresh_from_db()
        self.assertEqual(self.closed_class.name, "Lớp đã đóng")

    def test_closed_class_detail_has_no_management_actions(self):
        learner = Learner.objects.create(full_name="Học viên của lớp")
        Enrollment.objects.create(
            learner=learner,
            training_class=self.closed_class,
        )

        response = self.client.get(
            reverse("class-detail", args=(self.closed_class.pk,))
        )

        self.assertContains(response, self.closed_class.name)
        self.assertContains(response, learner.full_name)
        for action in (
            "Sửa thông tin",
            "Phân công giáo viên",
            "Chuyển lớp",
            "Lịch sử chuyển lớp",
            "Ghi danh học viên",
            "Đóng lớp",
            "Hủy ghi danh",
        ):
            with self.subTest(action=action):
                self.assertNotContains(response, action)
        self.assertNotContains(response, "Hành động</th>")

    def test_closed_class_blocks_teacher_assignment_routes(self):
        teacher = Teacher.objects.create(full_name="Giáo viên lớp đóng")
        assignment = ClassAssignment.objects.create(
            training_class=self.closed_class,
            teacher=teacher,
        )
        class_detail_url = reverse(
            "class-detail",
            args=(self.closed_class.pk,),
        )

        assignment_response = self.client.post(
            reverse("class-assignments", args=(self.closed_class.pk,)),
            {"teacher": teacher.pk},
            follow=True,
        )
        self.assertContains(
            assignment_response,
            "Lớp đã đóng, chỉ có thể xem thông tin lớp.",
        )
        self.assertEqual(
            ClassAssignment.objects.filter(
                training_class=self.closed_class,
                teacher=teacher,
                is_active=True,
            ).count(),
            1,
        )

        remove_response = self.client.post(
            reverse(
                "class-assignment-remove",
                args=(self.closed_class.pk, assignment.pk),
            ),
            {"reason": "Không thay đổi được lớp đã đóng"},
            follow=True,
        )
        self.assertContains(
            remove_response,
            "Lớp đã đóng, chỉ có thể xem thông tin lớp.",
        )
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)
        self.assertEqual(remove_response.redirect_chain[-1][0], class_detail_url)
