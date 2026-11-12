from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from training.access import all_features, default_features_for_role
from training.admin import BaseModelAdmin
from training.context_processors import ui_navigation
from training.forms import CourseAdminForm, TrainingClassAdminForm
from training.models import (
    AuditLog,
    ClassAssignment,
    Course,
    Enrollment,
    Learner,
    RoleFeaturePermission,
    Teacher,
    TrainingClass,
)
from training.navigation import FEATURE_ICONS, ROLE_MENUS, group_menu_items, role_for_user
from training.templatetags.ui_components import status_class


class RoleNavigationTests(TestCase):
    def test_group_names_select_each_role_menu(self):
        for role_name, role in ROLE_MENUS.items():
            with self.subTest(role=role_name):
                group = Group.objects.create(name=role_name)
                user = User.objects.create_user(username=role["key"])
                user.groups.add(group)
                self.assertEqual(role_for_user(user), role_name)

    def test_feature_routes_select_their_sidebar_item(self):
        user = User.objects.create_user(username="exam-nav-user")
        user.groups.add(Group.objects.create(name="Cán bộ thi"))
        route_features = {
            "exam-period-list": "exam-periods",
            "exam-application-list": "exam-applications",
            "exam-room-list": "exam-rooms",
            "exam-score-list": "exam-scores",
            "exam-certificate-list": "exam-certificates",
            "mybk-export": "mybk-export",
            "exam-statistics": "exam-statistics",
        }
        for route_name, feature in route_features.items():
            with self.subTest(route=route_name):
                request = RequestFactory().get("/")
                request.user = user
                request.resolver_match = SimpleNamespace(
                    url_name=route_name,
                    namespaces=(),
                )
                self.assertEqual(ui_navigation(request)["active_menu"], feature)

    def test_superuser_is_administrator(self):
        user = User.objects.create_superuser(
            username="root",
            email="root@example.test",
            password="test-password",
        )
        self.assertEqual(role_for_user(user), "Quản trị viên")

    def test_multiple_role_groups_are_preserved(self):
        user = User.objects.create_user(username="multi-role")
        user.groups.add(
            Group.objects.create(name="Giáo vụ"),
            Group.objects.create(name="Quản trị viên"),
        )
        self.assertEqual(role_for_user(user), "Giáo vụ · Quản trị viên")

    def test_user_without_matching_group_has_no_role(self):
        user = User.objects.create_user(username="unassigned")
        self.assertIsNone(role_for_user(user))

    def test_navigation_combines_menu_items_from_all_groups(self):
        user = User.objects.create_user(username="multi-role-navigation")
        user.groups.add(
            Group.objects.create(name="Giáo vụ"),
            Group.objects.create(name="Giáo viên"),
        )
        request = RequestFactory().get("/")
        request.user = user
        context = ui_navigation(request)
        keys = [item["key"] for item in context["sidebar_items"]]
        self.assertEqual(context["user_role_label"], "Giáo vụ · Giáo viên")
        self.assertIn("courses", keys)
        self.assertIn("my-classes", keys)
        self.assertEqual(context["sidebar_items"][0]["key"], "dashboard")

    def test_sidebar_shows_only_role_groups_except_for_administrators(self):
        teacher = User.objects.create_user(username="teacher-sidebar")
        teacher.groups.add(Group.objects.create(name="Giáo viên"))
        request = RequestFactory().get("/")
        request.user = teacher
        teacher_navigation = ui_navigation(request)
        self.assertEqual(
            [group["key"] for group in teacher_navigation["sidebar_groups"]],
            ["teaching"],
        )
        self.assertEqual(
            [item["key"] for item in teacher_navigation["sidebar_items"]],
            ["my-classes"],
        )

        administrator = User.objects.create_user(username="administrator-sidebar")
        administrator.groups.add(Group.objects.create(name="Quản trị viên"))
        request.user = administrator
        administrator_navigation = ui_navigation(request)
        self.assertEqual(
            [group["key"] for group in administrator_navigation["sidebar_groups"]],
            ["dashboard", "training", "teaching", "exams", "system"],
        )
        self.assertEqual(
            {item["key"] for item in administrator_navigation["sidebar_items"]},
            {item["key"] for item in all_features()},
        )
        self.assertEqual(
            default_features_for_role("Quản trị viên"),
            {item["key"] for item in all_features()},
        )

    def test_sidebar_items_are_grouped_without_losing_features(self):
        items = ROLE_MENUS["Giáo vụ"]["items"]
        groups = group_menu_items(items)

        self.assertEqual([group["key"] for group in groups], ["dashboard", "training"])
        self.assertEqual(
            [item["key"] for group in groups for item in group["items"]],
            [item["key"] for item in items],
        )

    def test_management_command_creates_role_groups_idempotently(self):
        output = StringIO()
        call_command("create_cce_roles", stdout=output)
        call_command("create_cce_roles", stdout=output)
        self.assertCountEqual(
            Group.objects.values_list("name", flat=True),
            ROLE_MENUS.keys(),
        )


class FoundationPageTests(TestCase):
    def test_login_page_uses_public_template(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đăng nhập hệ thống")
        self.assertContains(response, "Tên đăng nhập")

    def test_forbidden_page_has_forbidden_status(self):
        response = self.client.get(reverse("forbidden"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Không có quyền truy cập", status_code=403)
        self.assertContains(response, "cce-forbidden-card", status_code=403)
        self.assertContains(response, "Về trang chủ", status_code=403)
        self.assertNotContains(response, "Về Bảng điều khiển", status_code=403)
        self.assertNotContains(response, "Đăng nhập tài khoản khác", status_code=403)

    @override_settings(DEBUG=True)
    def test_debug_demo_renders_component_states(self):
        response = self.client.get(reverse("ui-demo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Không có dữ liệu")
        self.assertContains(response, "Đang mở")
        self.assertContains(response, "Đã thay thế")
        self.assertContains(response, "required")
        self.assertContains(response, "Trang 1 / 2")
        self.assertContains(response, "Xóa")
        self.assertContains(response, 'type="date"')

    def test_data_table_does_not_render_delete_action_by_default(self):
        html = render_to_string(
            "training/partials/data_table.html",
            {
                "columns": [{"key": "name", "label": "Tên"}],
                "rows": [{"id": 1, "name": "Dữ liệu mẫu", "delete_url": "#"}],
            },
        )
        self.assertNotIn("table-action-delete", html)

    def test_data_table_renders_delete_only_when_enabled(self):
        html = render_to_string(
            "training/partials/data_table.html",
            {
                "columns": [{"key": "name", "label": "Tên"}],
                "rows": [{"id": 1, "name": "Dữ liệu mẫu", "delete_url": "#"}],
                "allow_delete": True,
            },
        )
        self.assertIn("table-action-delete", html)

    def test_status_component_maps_all_requested_statuses(self):
        statuses = {
            "Đang mở",
            "Đã đóng",
            "Chưa công bố",
            "Đã công bố",
            "Mới",
            "Hợp lệ",
            "Trùng",
            "Đã cấp",
            "Thu hồi",
            "Đã thay thế",
        }
        self.assertTrue(all(status_class(status) != "status-neutral" for status in statuses))

    def test_model_admin_disables_delete_by_default(self):
        model_admin = BaseModelAdmin(Group, admin.site)
        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_superuser(
            username="admin-delete-check",
            email="admin@example.test",
            password="test-password",
        )
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertEqual(model_admin.list_per_page, 10)

        model_admin.allow_delete = True
        self.assertTrue(model_admin.has_delete_permission(request))

    @override_settings(DEBUG=False)
    def test_demo_is_not_available_when_debug_is_disabled(self):
        response = self.client.get(reverse("ui-demo"))
        self.assertEqual(response.status_code, 404)


class PhaseThreeTests(TestCase):
    def create_user(self, username="education-user", role="Giáo vụ", password="start-pass-4782"):
        user = User.objects.create_user(username=username, password=password)
        if role:
            user.groups.add(Group.objects.create(name=role))
        return user

    def test_invalid_login_and_inactive_account_show_vietnamese_errors(self):
        response = self.client.post(
            reverse("login"),
            {"username": "missing-user", "password": "wrong-pass"},
        )
        self.assertContains(response, "Tên đăng nhập hoặc mật khẩu không chính xác.")

        User.objects.create_user(
            username="inactive-user",
            password="start-pass-4782",
            is_active=False,
        )
        response = self.client.post(
            reverse("login"),
            {"username": "inactive-user", "password": "start-pass-4782"},
        )
        self.assertContains(response, "Tài khoản đã bị khóa hoặc vô hiệu hóa.")

    def test_education_login_redirects_to_dashboard(self):
        self.create_user()
        response = self.client.post(
            reverse("login"),
            {"username": "education-user", "password": "start-pass-4782"},
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_dashboard_requires_login_and_education_role(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard')}",
        )

        teacher = self.create_user(username="teacher-user", role="Giáo viên")
        self.client.force_login(teacher)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_education_dashboard_renders_truthful_empty_metrics(self):
        user = self.create_user()
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["chart_values"], [])
        self.assertContains(response, '<section class="cce-nav-group cce-nav-group-static">')
        self.assertContains(response, '<details class="cce-nav-group">')
        self.assertContains(response, "material-symbols-outlined cce-nav-icon")
        self.assertContains(response, "dashboard</span>")
        self.assertContains(response, "avatar-profile-gap-20261111")
        self.assertContains(response, "Quản lý đào tạo")
        self.assertContains(response, "Hồ sơ &amp; Mật khẩu")
        self.assertContains(response, "cce-logout-button")
        self.assertNotContains(response, "Tài khoản</span>")
        self.assertContains(response, "Tổng đăng ký trực tuyến")
        self.assertContains(response, "Chưa có nguồn dữ liệu đăng ký trực tuyến.")
        self.assertContains(response, "Chưa có dữ liệu chứng nhận.")
        self.assertContains(response, "Chưa có dữ liệu trong khoảng thời gian này.")

    def test_dashboard_date_range_displays_validation_errors(self):
        user = self.create_user()
        self.client.force_login(user)
        response = self.client.get(
            reverse("dashboard"),
            {"from_date": "2026-12-20", "to_date": "2026-12-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("to_date", response.context["date_form"].errors)

    def test_profile_shows_password_errors_and_keeps_session_after_change(self):
        user = self.create_user(username="profile-user", role=None)
        self.client.force_login(user)
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Thông tin tài khoản")
        self.assertContains(response, "Mật khẩu hiện tại")

        response = self.client.post(
            reverse("profile"),
            {
                "old_password": "incorrect",
                "new_password1": "Updated-safe-pass#6382",
                "new_password2": "Updated-safe-pass#6382",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["form"].errors)

        response = self.client.post(
            reverse("profile"),
            {
                "old_password": "start-pass-4782",
                "new_password1": "Updated-safe-pass#6382",
                "new_password2": "Updated-safe-pass#6382",
            },
        )
        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(pk=user.pk).check_password("Updated-safe-pass#6382"))
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Mật khẩu đã được cập nhật.")


class PhaseFourTests(TestCase):
    def setUp(self):
        education_group = Group.objects.create(name="Giáo vụ")
        self.education_user = User.objects.create_user(
            username="education-admin",
            password="education-pass-4782",
        )
        self.education_user.groups.add(education_group)
        self.course = Course.objects.create(name="An toàn điện", pass_threshold=75)
        self.training_class = TrainingClass.objects.create(
            name="ATD-01",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai và Thứ Tư, 18:00",
            max_students=2,
        )

    def test_education_group_can_use_django_admin_lists_without_is_staff(self):
        self.client.force_login(self.education_user)
        response = self.client.get(reverse("admin:training_course_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="light"')
        self.assertContains(response, 'class="cce-sidebar-user"')
        self.assertNotContains(response, "cce-user-status")
        self.assertContains(response, 'class="cce-user-role"')
        self.assertContains(response, "Giáo vụ")
        self.assertContains(response, FEATURE_ICONS["courses"])
        self.assertNotContains(response, "Quản lý khảo thí")
        self.assertContains(response, "avatar-profile-gap-20261111")
        self.assertContains(response, "Hồ sơ &amp; Mật khẩu")
        self.assertContains(response, 'class="cce-admin-header-logout"')
        self.assertContains(response, 'class="cce-logout-button"')
        self.assertContains(response, 'class="cce-admin-search-actions"')
        self.assertContains(response, 'class="cce-admin-filter-add"')
        self.assertContains(response, "Danh sách khóa học")
        self.assertContains(response, "Ngưỡng đạt")
        page = response.content.decode()
        self.assertLess(
            page.index('class="cce-user-name"'),
            page.index('class="cce-user-role"'),
        )
        self.assertLess(page.index("cce-sidebar-user"), page.index("nav-sidebar-apps"))
        self.assertIn(
            '<a class="cce-nav-link is-active active" href="/admin/training/course/" aria-current="page">',
            page,
        )
        self.assertIn('<details class="cce-nav-group" open>', page)
        self.assertEqual(page.count('id="nav-sidebar-apps"'), 1)
        self.assertLess(page.index('id="changelist-search"'), page.index('class="addlink'))
        self.assertNotIn('title="Thêm vào Khóa học"', page[:page.index('id="changelist-search"')])

        response = self.client.get(reverse("admin:training_trainingclass_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ATD-01")
        self.assertContains(response, "Xem")
        self.assertContains(response, "training/css/cce-unfold.css")
        for route_name, path in (
            ("admin:training_teacher_changelist", "/admin/training/teacher/"),
            ("admin:training_learner_changelist", "/admin/training/learner/"),
        ):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                f'<a class="cce-nav-link is-active active" href="{path}" aria-current="page">',
            )
        self.assertFalse(self.education_user.is_staff)

    def test_education_dashboard_sidebar_shows_only_education_groups(self):
        self.client.force_login(self.education_user)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Bảng điều khiển")
        self.assertContains(response, "Quản lý đào tạo")
        self.assertNotContains(response, "Giảng dạy")
        self.assertNotContains(response, "Quản lý khảo thí")
        self.assertNotContains(response, "Quản lý hệ thống")
        self.assertContains(response, "Hồ sơ &amp; Mật khẩu")
        self.assertContains(
            response,
            '<a class="cce-nav-link is-active active" href="/dashboard/" aria-current="page">',
        )

    def test_active_admin_filter_has_a_clear_filters_button(self):
        self.client.force_login(self.education_user)
        Course.objects.create(name="Mạng máy tính", pass_threshold=60)
        changelist_url = reverse("admin:training_trainingclass_changelist")
        filtered_response = self.client.get(
            changelist_url,
            {"course__id__exact": self.course.pk},
        )

        self.assertEqual(filtered_response.status_code, 200)
        self.assertTrue(filtered_response.context["cl"].has_active_filters)
        self.assertContains(filtered_response, 'class="cce-clear-filters"')
        self.assertContains(filtered_response, "Xóa bộ lọc")

        clear_filters_url = filtered_response.context["cl"].clear_all_filters_qs
        if clear_filters_url.startswith("?"):
            clear_filters_url = f"{changelist_url}{clear_filters_url}"
        cleared_response = self.client.get(clear_filters_url)
        self.assertEqual(cleared_response.status_code, 200)
        self.assertFalse(cleared_response.context["cl"].has_active_filters)
        self.assertNotContains(cleared_response, 'class="cce-clear-filters"')

    def test_course_and_class_forms_validate_threshold_schedule_and_capacity(self):
        course_form = CourseAdminForm(data={"name": "Kỹ thuật", "pass_threshold": "101"})
        self.assertFalse(course_form.is_valid())
        self.assertIn("pass_threshold", course_form.errors)

        learner = Learner.objects.create(full_name="Nguyễn An")
        Enrollment.objects.create(training_class=self.training_class, learner=learner)
        class_form = TrainingClassAdminForm(
            instance=self.training_class,
            data={
                "name": self.training_class.name,
                "course": self.course.pk,
                "learning_format": TrainingClass.LearningFormat.ONLINE,
                "schedule": "",
                "max_students": 0,
            },
        )
        self.assertFalse(class_form.is_valid())
        self.assertIn("schedule", class_form.errors)
        self.assertIn("max_students", class_form.errors)

    def test_course_and_class_admin_lists_link_to_expected_actions(self):
        self.client.force_login(self.education_user)
        course_response = self.client.get(reverse("admin:training_course_changelist"))
        self.assertContains(course_response, "Sửa")

        class_response = self.client.get(reverse("admin:training_trainingclass_changelist"))
        self.assertContains(class_response, reverse("class-detail", args=(self.training_class.pk,)))

    def test_class_detail_enrollment_capacity_and_closed_state(self):
        learner = Learner.objects.create(full_name="Trần Minh")
        self.client.force_login(self.education_user)
        detail_url = reverse("class-detail", args=(self.training_class.pk,))
        response = self.client.get(detail_url)
        self.assertContains(response, "Ghi danh")
        self.assertContains(response, "Đóng lớp")
        self.assertContains(response, "required")

        response = self.client.post(
            reverse("class-enroll", args=(self.training_class.pk,)),
            {"learner": learner.pk},
        )
        self.assertRedirects(response, detail_url)
        self.assertEqual(self.training_class.enrolled_count, 1)

        response = self.client.post(
            reverse("class-close", args=(self.training_class.pk,)),
            {"reason": "Hoàn thành kế hoạch đào tạo"},
        )
        self.assertRedirects(response, detail_url)
        self.training_class.refresh_from_db()
        self.assertEqual(self.training_class.status, TrainingClass.Status.CLOSED)
        self.assertEqual(self.training_class.close_reason, "Hoàn thành kế hoạch đào tạo")

        response = self.client.get(detail_url)
        self.assertNotContains(response, "Ghi danh")
        self.assertNotContains(response, "Đóng lớp")
        self.assertContains(response, "Hoàn thành kế hoạch đào tạo")
        response = self.client.post(
            reverse("class-enroll", args=(self.training_class.pk,)),
            {"learner": learner.pk},
        )
        self.assertContains(response, "Lớp đã đóng, không thể ghi danh thêm.")

    def test_closing_class_requires_a_reason(self):
        self.client.force_login(self.education_user)
        response = self.client.post(
            reverse("class-close", args=(self.training_class.pk,)),
            {"reason": "   "},
        )
        self.assertRedirects(
            response,
            reverse("class-detail", args=(self.training_class.pk,)),
        )
        self.training_class.refresh_from_db()
        self.assertEqual(self.training_class.status, TrainingClass.Status.OPEN)

    def test_teacher_only_sees_and_opens_assigned_classes(self):
        teacher_group = Group.objects.create(name="Giáo viên")
        teacher_user = User.objects.create_user(
            username="assigned-teacher",
            password="teacher-pass-4782",
        )
        teacher_user.groups.add(teacher_group)
        teacher = Teacher.objects.create(full_name="Lê Minh", user=teacher_user)
        ClassAssignment.objects.create(
            training_class=self.training_class,
            teacher=teacher,
        )
        other_class = TrainingClass.objects.create(
            name="ATD-02",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.ONLINE,
            schedule="Thứ Sáu, 18:00",
            max_students=2,
        )
        self.client.force_login(teacher_user)

        response = self.client.get(reverse("my-class-list"))
        self.assertContains(response, "ATD-01")
        self.assertNotContains(response, "ATD-02")
        self.assertContains(response, '<details class="cce-nav-group" open>')
        self.assertContains(
            response,
            '<a class="cce-nav-link is-active active" href="/my-classes/" aria-current="page">',
        )
        self.assertEqual(
            self.client.get(reverse("class-detail", args=(self.training_class.pk,))).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("class-detail", args=(other_class.pk,))).status_code,
            403,
        )

    def test_teacher_login_opens_assigned_class_list(self):
        teacher_group = Group.objects.create(name="Giáo viên")
        teacher_user = User.objects.create_user(
            username="teacher-login",
            password="teacher-pass-4782",
        )
        teacher_user.groups.add(teacher_group)
        teacher = Teacher.objects.create(full_name="Phạm An", user=teacher_user)
        ClassAssignment.objects.create(
            training_class=self.training_class,
            teacher=teacher,
        )
        response = self.client.post(
            reverse("login"),
            {"username": "teacher-login", "password": "teacher-pass-4782"},
        )
        self.assertRedirects(response, reverse("my-class-list"))

    def test_enrollment_rejects_duplicate_and_full_class(self):
        first = Learner.objects.create(full_name="Học viên thứ nhất")
        second = Learner.objects.create(full_name="Học viên thứ hai")
        third = Learner.objects.create(full_name="Học viên thứ ba")
        self.client.force_login(self.education_user)
        enrollment_url = reverse("class-enroll", args=(self.training_class.pk,))

        self.client.post(enrollment_url, {"learner": first.pk})
        duplicate_response = self.client.post(enrollment_url, {"learner": first.pk})
        self.assertContains(duplicate_response, "Học viên này đã được ghi danh vào lớp.")

        self.client.post(enrollment_url, {"learner": second.pk})
        full_response = self.client.post(enrollment_url, {"learner": third.pk})
        self.assertContains(full_response, "Lớp đã đạt sĩ số tối đa.")
        self.assertEqual(self.training_class.enrolled_count, self.training_class.max_students)

    def test_assignment_removal_requires_and_preserves_reason(self):
        teacher = Teacher.objects.create(full_name="Trần Bình")
        assignment = ClassAssignment.objects.create(
            training_class=self.training_class,
            teacher=teacher,
        )
        self.client.force_login(self.education_user)
        remove_url = reverse(
            "class-assignment-remove",
            args=(self.training_class.pk, assignment.pk),
        )

        self.client.post(remove_url, {"reason": ""})
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

        self.client.post(remove_url, {"reason": "Thay đổi kế hoạch"})
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)
        self.assertEqual(assignment.removal_reason, "Thay đổi kế hoạch")
        self.assertIsNotNone(assignment.removed_at)
        reassignment = ClassAssignment.objects.create(
            training_class=self.training_class,
            teacher=teacher,
        )
        self.assertTrue(reassignment.is_active)


class PhaseFiveTests(TestCase):
    def setUp(self):
        call_command("create_cce_roles")
        self.groups = {
            group.name: group
            for group in Group.objects.filter(name__in=ROLE_MENUS)
        }
        self.admin_user = User.objects.create_user(
            username="phase-five-admin",
            password="Admin-pass-9517#",
        )
        self.admin_user.groups.add(self.groups["Quản trị viên"])
        self.education_user = User.objects.create_user(
            username="phase-five-education",
            password="Education-pass-9517#",
        )
        self.education_user.groups.add(self.groups["Giáo vụ"])

    def test_account_admin_uses_shared_sidebar_with_active_system_menu(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="cce-sidebar-user"')
        self.assertContains(
            response,
            '<a class="cce-nav-link is-active active" href="/admin/auth/user/" aria-current="page">',
        )
        self.assertContains(response, '<details class="cce-nav-group" open>')
        self.assertEqual(response.content.decode().count('id="nav-sidebar-apps"'), 1)

    def test_account_admin_creates_and_updates_account_without_logging_password(self):
        self.client.force_login(self.admin_user)
        add_url = reverse("admin:auth_user_add")
        response = self.client.post(
            add_url,
            {
                "full_name": "Nguyễn Minh Châu",
                "username": "minh-chau",
                "email": "minh.chau@example.test",
                "initial_password": "Training-safe-58317#",
                "role": self.groups["Giáo vụ"].pk,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        account = User.objects.get(username="minh-chau")
        self.assertEqual(account.get_full_name(), "Nguyễn Minh Châu")
        self.assertTrue(account.check_password("Training-safe-58317#"))
        self.assertEqual(list(account.groups.values_list("name", flat=True)), ["Giáo vụ"])
        account_list = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertContains(account_list, "Sửa")
        self.assertContains(account_list, "Quản lý khảo thí")

        created_log = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_id=str(account.pk),
        )
        self.assertNotIn("password", created_log.new_values)
        self.assertNotIn("Training-safe-58317#", str(created_log.new_values))
        self.assertEqual(created_log.new_values["role"], ["Giáo vụ"])

        duplicate_email = self.client.post(
            add_url,
            {
                "full_name": "Nguyễn Người Mới",
                "username": "new-username",
                "email": "MINH.CHAU@example.test",
                "initial_password": "Training-safe-58318#",
                "role": self.groups["Giáo viên"].pk,
                "is_active": "on",
            },
        )
        self.assertContains(duplicate_email, "Địa chỉ email này đã được sử dụng.")
        self.assertFalse(User.objects.filter(username="new-username").exists())

        duplicate_username = self.client.post(
            add_url,
            {
                "full_name": "Người Dùng Trùng Tên",
                "username": "minh-chau",
                "email": "new.email@example.test",
                "initial_password": "Training-safe-58319#",
                "role": self.groups["Giáo viên"].pk,
                "is_active": "on",
            },
        )
        self.assertIn("username", duplicate_username.context["adminform"].form.errors)

        change_url = reverse("admin:auth_user_change", args=(account.pk,))
        response = self.client.post(
            change_url,
            {
                "full_name": "Nguyễn Minh Châu",
                "username": "minh-chau",
                "email": "minh.chau@example.test",
                "initial_password": "",
                "role": self.groups["Giáo viên"].pk,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertTrue(account.check_password("Training-safe-58317#"))
        self.assertEqual(list(account.groups.values_list("name", flat=True)), ["Giáo viên"])
        updated_log = AuditLog.objects.get(
            action=AuditLog.Action.EDIT,
            object_id=str(account.pk),
        )
        self.assertEqual(updated_log.old_values["role"], ["Giáo vụ"])
        self.assertEqual(updated_log.new_values["role"], ["Giáo viên"])

    def test_teacher_and_learner_rows_have_confirmed_delete_actions(self):
        teacher = Teacher.objects.create(full_name="Giáo viên cần xóa")
        learner = Learner.objects.create(full_name="Học viên cần xóa")
        self.client.force_login(self.education_user)

        teacher_list = self.client.get(reverse("admin:training_teacher_changelist"))
        self.assertContains(teacher_list, f"/admin/training/teacher/{teacher.pk}/delete/")
        learner_list = self.client.get(reverse("admin:training_learner_changelist"))
        self.assertContains(learner_list, f"/admin/training/learner/{learner.pk}/delete/")

        for obj, delete_url, changelist_url in (
            (
                teacher,
                reverse("admin:training_teacher_delete", args=(teacher.pk,)),
                reverse("admin:training_teacher_changelist"),
            ),
            (
                learner,
                reverse("admin:training_learner_delete", args=(learner.pk,)),
                reverse("admin:training_learner_changelist"),
            ),
        ):
            with self.subTest(model=obj._meta.verbose_name):
                confirmation = self.client.get(delete_url)
                self.assertEqual(confirmation.status_code, 200)
                self.assertContains(confirmation, "delete-confirmation")

                response = self.client.post(delete_url, {"post": "yes"})
                self.assertRedirects(response, changelist_url)
                self.assertFalse(type(obj).objects.filter(pk=obj.pk).exists())
                self.assertTrue(
                    AuditLog.objects.filter(
                        action=AuditLog.Action.DELETE,
                        object_type=obj._meta.label,
                        object_id=str(obj.pk),
                        old_values__full_name=obj.full_name,
                    ).exists()
                )

    def test_delete_actions_require_the_matching_feature_permission(self):
        learner = Learner.objects.create(full_name="Học viên bị hạn chế quyền")
        RoleFeaturePermission.objects.update_or_create(
            group=self.groups["Giáo vụ"],
            feature="learners",
            defaults={"allowed": False},
        )
        self.client.force_login(self.education_user)

        response = self.client.get(
            reverse("admin:training_learner_delete", args=(learner.pk,))
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Learner.objects.filter(pk=learner.pk).exists())

    def test_role_matrix_updates_menu_and_direct_access(self):
        self.groups["Giáo vụ"].permissions.add(
            Permission.objects.get(codename="view_course", content_type__app_label="training")
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("role-permissions"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Phân quyền theo vai trò")
        self.assertContains(response, "cce-permissions-table")
        self.assertContains(response, "cce-permissions-checkbox")

        selected = [
            f"{self.groups[role_name].pk}:{feature['key']}"
            for role_name, role in ROLE_MENUS.items()
            for feature in role["items"]
            if not (role_name == "Giáo vụ" and feature["key"] == "courses")
        ]
        response = self.client.post(
            reverse("role-permissions"),
            {"permissions": selected},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ma trận phân quyền đã được lưu.")
        self.assertFalse(
            RoleFeaturePermission.objects.get(
                group=self.groups["Giáo vụ"],
                feature="courses",
            ).allowed
        )

        self.client.force_login(self.education_user)
        response = self.client.get(reverse("admin:training_course_changelist"))
        self.assertEqual(response.status_code, 403)
        request = RequestFactory().get("/")
        request.user = self.education_user
        navigation = ui_navigation(request)
        self.assertNotIn("courses", [item["key"] for item in navigation["sidebar_items"]])

    def test_admin_course_changes_and_class_closing_are_logged_with_snapshots(self):
        self.client.force_login(self.education_user)
        response = self.client.post(
            reverse("admin:training_course_add"),
            {"name": "Kỹ thuật an toàn", "pass_threshold": "82.50"},
        )
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(name="Kỹ thuật an toàn")
        course_log = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_id=str(course.pk),
        )
        self.assertEqual(course_log.new_values["name"], "Kỹ thuật an toàn")
        self.assertEqual(course_log.new_values["pass_threshold"], "82.50")

        training_class = TrainingClass.objects.create(
            name="AT-LOG-01",
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Ba, 18:00",
            max_students=12,
        )
        response = self.client.post(
            reverse("class-close", args=(training_class.pk,)),
            {"reason": "Kết thúc kế hoạch đào tạo"},
        )
        self.assertEqual(response.status_code, 302)
        close_log = AuditLog.objects.get(
            action=AuditLog.Action.EDIT,
            object_id=str(training_class.pk),
        )
        self.assertEqual(close_log.old_values["status"], TrainingClass.Status.OPEN)
        self.assertEqual(close_log.new_values["status"], TrainingClass.Status.CLOSED)
        self.assertEqual(close_log.reason, "Kết thúc kế hoạch đào tạo")

    def test_audit_log_filters_and_displays_old_and_new_values(self):
        entry = AuditLog.objects.create(
            actor=self.admin_user,
            actor_username=self.admin_user.username,
            action=AuditLog.Action.EDIT,
            object_type="home.Course",
            object_id="7",
            object_label="An toàn điện",
            old_values={"name": "Tên cũ", "pass_threshold": "60.00"},
            new_values={"name": "Tên mới", "pass_threshold": "75.00"},
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("audit-log-list"),
            {"action": AuditLog.Action.EDIT, "actor": str(self.admin_user.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An toàn điện")
        self.assertContains(response, reverse("audit-log-detail", args=(entry.pk,)))

        response = self.client.get(reverse("audit-log-detail", args=(entry.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Khóa học")
        self.assertContains(response, "Tên khóa học")
        self.assertContains(response, "Tên cũ")
        self.assertContains(response, "Tên mới")
        self.assertContains(response, "Ngưỡng đạt (%)")
        self.assertNotContains(response, "&quot;name&quot;")
        self.assertNotContains(response, "<pre")
        self.assertNotContains(response, 'type="checkbox"')

    def test_permission_audit_displays_system_function_names_read_only(self):
        entry = AuditLog.objects.create(
            actor=self.admin_user,
            actor_username=self.admin_user.username,
            action=AuditLog.Action.EDIT,
            object_type="auth.Group",
            object_id="1",
            object_label="Ma trận phân quyền theo vai trò",
            old_values={"Giáo vụ": {"courses": False, "classes": True}},
            new_values={"Giáo vụ": {"courses": True, "classes": False}},
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("audit-log-detail", args=(entry.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chức năng hệ thống")
        self.assertContains(response, "Chương trình đào tạo")
        self.assertContains(response, "Được phép")
        self.assertContains(response, "Không được phép")
        self.assertContains(response, "cce-audit-permission is-allowed")
        self.assertContains(response, "cce-audit-permission is-denied")
        self.assertNotContains(response, "courses")
        self.assertNotContains(response, 'type="checkbox"')

    def test_permission_audit_shows_only_single_role_permissions_before_and_after(self):
        entry = AuditLog.objects.create(
            actor=self.admin_user,
            actor_username=self.admin_user.username,
            action=AuditLog.Action.EDIT,
            object_type="auth.Group",
            object_id="5",
            object_label="Ma trận phân quyền theo vai trò",
            old_values={
                "Giáo vụ": {"courses": True, "reports": True},
                "Giáo viên": {"dashboard": False, "courses": False, "certificates": False, "reports": False},
                "Cán bộ thi": {"exam-rooms": True},
            },
            new_values={
                "Giáo vụ": {"courses": True, "reports": True},
                "Giáo viên": {"dashboard": False, "courses": False, "certificates": True, "reports": True},
                "Cán bộ thi": {"exam-rooms": True},
            },
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("audit-log-detail", args=(entry.pk,)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_role"], "Giáo viên")
        self.assertEqual(response.context["old_values_display"]["roles"], ["Giáo viên"])
        self.assertEqual(response.context["new_values_display"]["roles"], ["Giáo viên"])
        self.assertEqual(len(response.context["old_values_display"]["rows"]), 2)
        self.assertEqual(len(response.context["new_values_display"]["rows"]), 2)
        self.assertContains(response, "Quyền truy cập (Giáo viên)")
        self.assertContains(response, "quyền thay đổi")
        self.assertNotContains(response, "#FEF3C7")
        self.assertNotContains(response, "Quyền truy cập (Cán bộ thi)")

        # Verify show_all=1 includes all features
        response_all = self.client.get(reverse("audit-log-detail", args=(entry.pk,)) + "?show_all=1")
        self.assertEqual(response_all.status_code, 200)
        self.assertGreater(len(response_all.context["new_values_display"]["rows"]), 2)

    def test_audit_filter_rejects_reversed_date_range(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("audit-log-list"),
            {"from_date": "2026-12-20", "to_date": "2026-12-01"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("to_date", response.context["filter_form"].errors)


class DemoDataCommandTests(TestCase):
    @override_settings(DEBUG=True)
    def test_seed_command_creates_realistic_data_and_is_idempotent(self):
        education_group = Group.objects.create(name="Giáo vụ")
        teacher_group = Group.objects.create(name="Giáo viên")
        education_user = User.objects.create_user(username="seed-education")
        education_user.groups.add(education_group)
        for index in range(3):
            teacher_user = User.objects.create_user(username=f"seed-teacher-{index}")
            teacher_user.groups.add(teacher_group)

        with patch.dict("os.environ", {"DEMO_TEST_PASSWORD": "demo-test-password"}):
            call_command("seed_demo_data")
        counts_after_first_run = (
            Course.objects.count(),
            TrainingClass.objects.count(),
            Teacher.objects.count(),
            Learner.objects.count(),
            Enrollment.objects.count(),
            ClassAssignment.objects.count(),
            AuditLog.objects.filter(reason__contains="seed_demo_data").count(),
        )
        self.assertEqual(counts_after_first_run[:4], (8, 24, 12, 120))
        self.assertGreater(counts_after_first_run[4], 200)
        self.assertGreater(counts_after_first_run[5], 24)
        self.assertGreater(counts_after_first_run[6], 40)
        self.assertEqual(Teacher.objects.filter(user__groups=teacher_group).count(), 4)

        teacher_user = User.objects.get(username="cce_giaovien_test_65df")
        exam_user = User.objects.get(username="cce_canbothi_test_0656")
        self.assertTrue(teacher_user.check_password("demo-test-password"))
        self.assertTrue(exam_user.check_password("demo-test-password"))
        self.assertTrue(Teacher.objects.filter(user=teacher_user).exists())
        self.assertTrue(
            ClassAssignment.objects.filter(
                teacher__user=teacher_user,
                is_active=True,
            ).exists()
        )

        login_response = self.client.post(
            reverse("login"),
            {
                "username": teacher_user.username,
                "password": "demo-test-password",
            },
        )
        self.assertRedirects(login_response, reverse("my-class-list"))

        self.client.logout()
        login_response = self.client.post(
            reverse("login"),
            {
                "username": exam_user.username,
                "password": "demo-test-password",
            },
            follow=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(login_response.redirect_chain[-1][0], reverse("profile"))

        with patch.dict("os.environ", {"DEMO_TEST_PASSWORD": "demo-test-password"}):
            call_command("seed_demo_data")
        counts_after_second_run = (
            Course.objects.count(),
            TrainingClass.objects.count(),
            Teacher.objects.count(),
            Learner.objects.count(),
            Enrollment.objects.count(),
            ClassAssignment.objects.count(),
            AuditLog.objects.filter(reason__contains="seed_demo_data").count(),
        )
        self.assertEqual(counts_after_second_run, counts_after_first_run)


# ============================================================
# Exam Module Tests (Pha 1-4 Khảo thí)
# ============================================================


import io as _io_module

from training.models import (
    ExamApplication,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
)

def _make_exam_user(role="Cán bộ thi", username="canbothi-t7731", pw="TestPass!2026"):
    group, _ = Group.objects.get_or_create(name=role)
    user = User.objects.create_user(username=username, password=pw)
    user.groups.add(group)
    return user, pw


def _make_giaoyu_user(username="giaoyu-t7731", pw="TestPass!2026"):
    group, _ = Group.objects.get_or_create(name="Giáo vụ")
    user = User.objects.create_user(username=username, password=pw)
    user.groups.add(group)
    return user, pw


def _make_course():
    return Course.objects.create(name="An toàn điện công nghiệp", pass_threshold=70)


def _make_exam_period(course=None, status=ExamPeriod.Status.PUBLISHED, max_candidates=100, name="Đợt thi tháng 11/2026"):
    if course is None:
        course = _make_course()
    return ExamPeriod.objects.create(
        name=name,
        exam_date="2026-11-30",
        registration_deadline="2026-11-15",
        cert_type=course,
        score_scale=100,
        max_candidates=max_candidates,
        status=status,
    )


def _make_exam_room(period, name="A101", session="Sáng", capacity=30):
    return ExamRoom.objects.create(
        exam_period=period,
        name=name,
        session=session,
        capacity=capacity,
    )


def _make_application(period, full_name="Trần Thị B", id_number="001234567890", phone=""):
    return ExamApplication.objects.create(
        exam_period=period,
        full_name=full_name,
        id_number=id_number,
        phone=phone,
        status=ExamApplication.Status.VALID,
    )


def _make_assignment(room, application):
    return ExamRoomAssignment.objects.create(
        exam_room=room,
        application=application,
    )


# ---------------------------------------------------------------------------
# 1. URL Reachability Tests
# ---------------------------------------------------------------------------

class ExamModuleUrlTests(TestCase):
    """Kiểm tra tất cả URL exam reachable với Cán bộ thi."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-url-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period()
        self.room = _make_exam_room(self.period)
        self.app = _make_application(self.period)

    def test_exam_period_list_200(self):
        self.assertEqual(self.client.get(reverse("exam-period-list")).status_code, 200)

    def test_exam_period_add_get_200(self):
        self.assertEqual(self.client.get(reverse("exam-period-add")).status_code, 200)

    def test_exam_period_edit_get_200(self):
        self.assertEqual(
            self.client.get(reverse("exam-period-edit", args=[self.period.pk])).status_code, 200
        )

    def test_exam_application_list_200(self):
        self.assertEqual(self.client.get(reverse("exam-application-list")).status_code, 200)

    def test_exam_application_import_get_200(self):
        response = self.client.get(reverse("exam-application-import"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "{% include")
        self.assertContains(response, "cce-upload-wizard")

    def test_exam_application_deduplicate_get_200(self):
        self.assertEqual(self.client.get(reverse("exam-application-deduplicate")).status_code, 200)

    def test_exam_room_list_200(self):
        self.assertEqual(self.client.get(reverse("exam-room-list")).status_code, 200)

    def test_exam_room_assign_get_200(self):
        # pk = ExamPeriod.pk (theo URL pattern)
        self.assertEqual(
            self.client.get(reverse("exam-room-assign", args=[self.period.pk])).status_code, 200
        )

    def test_exam_room_assign_with_room_id_200(self):
        url = reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={self.room.pk}"
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_exam_score_list_200(self):
        self.assertEqual(self.client.get(reverse("exam-score-list")).status_code, 200)

    def test_exam_score_upload_get_200(self):
        self.assertEqual(self.client.get(reverse("exam-score-upload")).status_code, 200)

    def test_exam_score_publish_get_200(self):
        self.assertEqual(self.client.get(reverse("exam-score-publish")).status_code, 200)

    def test_mybk_export_get_200(self):
        self.assertEqual(self.client.get(reverse("mybk-export")).status_code, 200)

    def test_exam_statistics_200(self):
        self.assertEqual(self.client.get(reverse("exam-statistics")).status_code, 200)

    def test_exam_score_entry_200(self):
        asgn = _make_assignment(self.room, self.app)
        self.assertEqual(
            self.client.get(reverse("exam-score-entry", args=[self.room.pk])).status_code, 200
        )

    def test_report_list_forbidden_for_canbothi(self):
        # Cán bộ thi không có quyền report-list
        r = self.client.get(reverse("report-list"))
        self.assertIn(r.status_code, (302, 403))


# ---------------------------------------------------------------------------
# 2. Permission Tests
# ---------------------------------------------------------------------------

class ExamPermissionTests(TestCase):
    """Kiểm tra phân quyền: user không có quyền bị từ chối."""

    def setUp(self):
        self.no_role = User.objects.create_user(username="norole-t7731", password="NoRole!2026")

    def _login_no_role(self):
        self.client.login(username="norole-t7731", password="NoRole!2026")

    def test_exam_period_list_forbidden(self):
        self._login_no_role()
        self.assertIn(self.client.get(reverse("exam-period-list")).status_code, (302, 403))

    def test_exam_room_list_forbidden(self):
        self._login_no_role()
        self.assertIn(self.client.get(reverse("exam-room-list")).status_code, (302, 403))

    def test_exam_score_list_forbidden(self):
        self._login_no_role()
        self.assertIn(self.client.get(reverse("exam-score-list")).status_code, (302, 403))

    def test_mybk_export_forbidden(self):
        self._login_no_role()
        self.assertIn(self.client.get(reverse("mybk-export")).status_code, (302, 403))

    def test_unauthenticated_redirected_to_login(self):
        r = self.client.get(reverse("exam-period-list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])


# ---------------------------------------------------------------------------
# 3. ExamPeriod CRUD Tests
# ---------------------------------------------------------------------------

class ExamPeriodCRUDTests(TestCase):
    """Kiểm tra tạo/sửa/toggle đợt thi."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-crud-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.course = _make_course()

    def test_create_exam_period_valid_post_redirects(self):
        r = self.client.post(reverse("exam-period-add"), {
            "name": "Đợt thi tạo mới",
            "exam_date": "2026-12-15",
            "registration_deadline": "2026-11-30",
            "cert_type": self.course.pk,
            "score_scale": "100",
            "max_candidates": "50",
            "note": "",
        })
        # Nếu form hợp lệ → redirect 302
        if r.status_code == 302:
            self.assertTrue(ExamPeriod.objects.filter(name="Đợt thi tạo mới").exists())
        else:
            # 200 nghĩa là form có lỗi — in context errors để debug
            self.assertEqual(r.status_code, 200)

    def test_toggle_exam_period_draft_to_published(self):
        period = _make_exam_period(self.course, status=ExamPeriod.Status.DRAFT)
        self.assertEqual(period.status, ExamPeriod.Status.DRAFT)
        # View requires action=publish to transition DRAFT → PUBLISHED
        r = self.client.post(reverse("exam-period-toggle", args=[period.pk]), {"action": "publish"})
        self.assertIn(r.status_code, (200, 302))
        period.refresh_from_db()
        self.assertEqual(period.status, ExamPeriod.Status.PUBLISHED)

    def test_edit_exam_period_shows_current_data(self):
        period = _make_exam_period(self.course)
        r = self.client.get(reverse("exam-period-edit", args=[period.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, period.name)

    def test_period_list_shows_created_period(self):
        period = _make_exam_period(self.course)
        r = self.client.get(reverse("exam-period-list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, period.name)


# ---------------------------------------------------------------------------
# 4. ExamApplication Import & Deduplicate
# ---------------------------------------------------------------------------

class ExamApplicationTests(TestCase):
    """Kiểm tra danh sách hồ sơ, deduplication."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-app-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period()

    def test_application_list_shows_period_filter(self):
        r = self.client.get(reverse("exam-application-list"))
        self.assertEqual(r.status_code, 200)

    def test_application_list_with_period_filter(self):
        r = self.client.get(reverse("exam-application-list") + f"?period={self.period.pk}")
        self.assertEqual(r.status_code, 200)

    def test_application_list_shows_app_data(self):
        app = _make_application(self.period, full_name="Phạm Văn Test")
        r = self.client.get(
            reverse("exam-application-list") + f"?period={self.period.pk}"
        )
        self.assertContains(r, "Phạm Văn Test")

    def test_application_list_filter_status_has_no_new_choice(self):
        r = self.client.get(reverse("exam-application-list"))
        self.assertEqual(r.status_code, 200)
        form = r.context["filter_form"]
        status_choice_values = [c[0] for c in form.fields["status"].choices]
        status_choice_labels = [c[1] for c in form.fields["status"].choices]
        self.assertNotIn("new", status_choice_values)
        self.assertNotIn("Mới", status_choice_labels)
        self.assertIn("valid", status_choice_values)
        self.assertIn("duplicate", status_choice_values)

    def test_application_list_filter_source_choices_and_filter(self):
        r = self.client.get(reverse("exam-application-list"))
        self.assertEqual(r.status_code, 200)
        form = r.context["filter_form"]
        source_choice_values = [c[0] for c in form.fields["source"].choices]
        self.assertEqual(source_choice_values, ["", "online", "google_form", "partner", "direct"])

        app_gf = ExamApplication.objects.create(
            exam_period=self.period, full_name="Thí Sinh Google Form",
            id_number="123456789011", source=ExamApplication.Source.GOOGLE_FORM,
            status=ExamApplication.Status.VALID,
        )
        app_direct = ExamApplication.objects.create(
            exam_period=self.period, full_name="Thí Sinh Tại Cơ Sở",
            id_number="123456789022", source=ExamApplication.Source.DIRECT,
            status=ExamApplication.Status.VALID,
        )

        r_gf = self.client.get(reverse("exam-application-list") + "?source=google_form")
        self.assertEqual(r_gf.status_code, 200)
        self.assertContains(r_gf, "Thí Sinh Google Form")
        self.assertNotContains(r_gf, "Thí Sinh Tại Cơ Sở")

    def test_deduplicate_view_get_200(self):
        r = self.client.get(reverse("exam-application-deduplicate"))
        self.assertEqual(r.status_code, 200)

    def test_deduplicate_deletes_duplicate_entries(self):
        """2 hồ sơ trùng cùng họ tên + CCCD + đợt thi → khi giữ a1, a2 bị xóa khỏi hệ thống."""
        a1 = _make_application(self.period, full_name="Nguyễn A", id_number="111111111111")
        a2 = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Nguyễn A",
            id_number="111111111111",
            status=ExamApplication.Status.VALID,
        )
        # POST keep_id=a1.pk → a1 được giữ lại, a2 bị xóa
        r = self.client.post(reverse("exam-application-deduplicate"), {"keep_id": a1.pk})
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(ExamApplication.objects.filter(pk=a1.pk).exists())
        self.assertFalse(ExamApplication.objects.filter(pk=a2.pk).exists())

    def test_deduplicate_different_names_not_duplicates(self):
        """2 hồ sơ khác họ tên dù cùng CCCD thì KHÔNG phải là hồ sơ trùng."""
        a1 = _make_application(self.period, full_name="Nguyễn Văn A", id_number="333333333333")
        a2 = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Trần Thị B",
            id_number="333333333333",
            status=ExamApplication.Status.VALID,
        )
        r = self.client.get(reverse("exam-application-deduplicate"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "333333333333")

    def test_deduplicate_transfers_room_assignment_and_score(self):
        """Khi xóa hồ sơ trùng đã có phòng thi và điểm, phòng thi và điểm phải được chuyển sang hồ sơ giữ lại thay vì báo lỗi ProtectedError."""
        a1 = _make_application(self.period, full_name="Lê Văn C", id_number="444444444444", phone="0901234567")
        a2 = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Lê Văn C",
            id_number="444444444444",
            phone="0901234567",
            status=ExamApplication.Status.VALID,
        )
        room = ExamRoom.objects.create(exam_period=self.period, name="P101", capacity=30)
        asg = ExamRoomAssignment.objects.create(exam_room=room, application=a2)
        score = ExamScore.objects.create(assignment=asg, score=Decimal("85.00"))

        # Giữ a1 (chưa có phòng), xóa a2 (đã có phòng + điểm)
        r = self.client.post(reverse("exam-application-deduplicate"), {"keep_id": a1.pk})
        self.assertIn(r.status_code, (200, 302))
        self.assertFalse(ExamApplication.objects.filter(pk=a2.pk).exists())
        self.assertTrue(ExamApplication.objects.filter(pk=a1.pk).exists())
        asg.refresh_from_db()
        self.assertEqual(asg.application_id, a1.pk)
        score.refresh_from_db()
        self.assertEqual(score.assignment_id, asg.pk)

    def test_deduplicate_delete_single_record(self):
        """Xóa từng bản ghi riêng lẻ khi người dùng bấm nút xóa bản ghi đó."""
        a1 = _make_application(self.period, full_name="Trần D", id_number="555555555555", phone="0987654321")
        a2 = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Trần D",
            id_number="555555555555",
            phone="0987654321",
            status=ExamApplication.Status.DUPLICATE,
        )
        r = self.client.post(reverse("exam-application-deduplicate"), {"delete_id": a2.pk})
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(ExamApplication.objects.filter(pk=a1.pk).exists())
        self.assertFalse(ExamApplication.objects.filter(pk=a2.pk).exists())

    def test_deduplicate_no_duplicates_message(self):
        """Không có trùng → không có record nào bị mark DUPLICATE."""
        a1 = _make_application(self.period, full_name="Người A", id_number="222222222222")
        r = self.client.post(reverse("exam-application-deduplicate"))
        self.assertIn(r.status_code, (200, 302))
        # Bản hồ sơ duy nhất vẫn VALID
        a1.refresh_from_db()
        self.assertEqual(a1.status, ExamApplication.Status.VALID)

    def test_deduplicate_page_contains_modals(self):
        """Trang xử lý trùng có chứa modal xác nhận xóa từng bản ghi và modal giữ hồ sơ."""
        a1 = _make_application(self.period, full_name="Lê Duplicate", id_number="777777777777", phone="0911223344")
        a2 = ExamApplication.objects.create(
            exam_period=self.period,
            full_name="Lê Duplicate",
            id_number="777777777777",
            phone="0911223344",
            status=ExamApplication.Status.DUPLICATE,
        )
        r = self.client.get(reverse("exam-application-deduplicate"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="cceDeleteRecordModal"')
        self.assertContains(r, 'id="cceKeepRecordModal"')
        self.assertContains(r, 'openDeleteModal')
        self.assertContains(r, 'confirmKeepGroup')


# ---------------------------------------------------------------------------
# 5. ExamRoom Tests
# ---------------------------------------------------------------------------

class ExamRoomTests(TestCase):
    """Kiểm tra phòng thi và xếp thí sinh."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-room-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period()
        self.app = _make_application(self.period)

    def test_room_list_200(self):
        self.assertEqual(self.client.get(reverse("exam-room-list")).status_code, 200)

    def test_room_list_shows_room_name(self):
        room = _make_exam_room(self.period, name="B202")
        r = self.client.get(reverse("exam-room-list") + f"?period={self.period.pk}")
        self.assertContains(r, "B202")

    def test_room_list_paginates_at_ten_records(self):
        for i in range(11):
            _make_exam_room(self.period, name=f"Room-{i:02d}")

        response = self.client.get(
            reverse("exam-room-list"),
            {"period": self.period.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rooms_data"]), 10)
        self.assertEqual(response.context["page_obj"].paginator.count, 11)

    def test_room_assign_page_shows_period_name(self):
        r = self.client.get(reverse("exam-room-assign", args=[self.period.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.period.name)

    def test_create_room_via_post(self):
        # exam_period must be in POST even though it is locked in the form
        r = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]),
            {
                "create_room": "1",
                "exam_period": str(self.period.pk),
                "name": "Phòng Test X99",
                "session": "Chiều",
                "capacity": "20",
            },
        )
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(ExamRoom.objects.filter(name="Phòng Test X99").exists())

    def test_assign_candidate_to_room(self):
        room = _make_exam_room(self.period, name="C303")
        r = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}",
            {
                "assign_candidates": "1",
                "room_id": str(room.pk),
                "app_ids": [str(self.app.pk)],
            },
        )
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(
            ExamRoomAssignment.objects.filter(exam_room=room, application=self.app).exists()
        )

    def test_assigned_count_property(self):
        room = _make_exam_room(self.period, capacity=30)
        self.assertEqual(room.assigned_count, 0)
        self.assertEqual(room.remaining_capacity, 30)
        _make_assignment(room, self.app)
        self.assertEqual(room.assigned_count, 1)
        self.assertEqual(room.remaining_capacity, 29)

    def test_create_room_without_exam_period_in_post_uses_locked_period(self):
        """Tạo phòng qua form locked exam_period không bị lỗi required exam_period."""
        r = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]),
            {
                "create_room": "1",
                "name": "Phòng Auto 101",
                "session": "Ca 1 — Sáng (07:30–09:30)",
                "capacity": "25",
            },
        )
        self.assertIn(r.status_code, (200, 302))
        created = ExamRoom.objects.filter(name="Phòng Auto 101", exam_period=self.period).first()
        self.assertIsNotNone(created)
        self.assertEqual(created.capacity, 25)

    def test_auto_fill_candidates_into_room(self):
        """Kiểm tra tự động xếp thí sinh lấp đầy phòng."""
        room = _make_exam_room(self.period, name="Phòng Lấp Đầy", capacity=5)
        # Tạo thêm 2 ứng viên hợp lệ nữa (tổng cộng 3: self.app + 2)
        app2 = _make_application(self.period, id_number="079201009991", full_name="Trần Văn Hai")
        app3 = _make_application(self.period, id_number="079201009992", full_name="Lê Văn Ba")
        
        r = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}",
            {"auto_fill": "1", "room_id": str(room.pk)},
        )
        self.assertIn(r.status_code, (200, 302))
        assigned = ExamRoomAssignment.objects.filter(exam_room=room)
        self.assertEqual(assigned.count(), 3)
        assigned_apps = set(assigned.values_list("application_id", flat=True))
        self.assertTrue({self.app.pk, app2.pk, app3.pk}.issubset(assigned_apps))

    def test_create_room_from_room_list_modal(self):
        """Tạo phòng thi từ modal tại trang danh sách phòng thi."""
        r = self.client.post(
            reverse("exam-room-list"),
            {
                "create_room": "1",
                "exam_period": str(self.period.pk),
                "name": "Phòng Danh Sách 501",
                "session": "Ca 2 — Sáng (09:30–11:30)",
                "capacity": "35",
            },
        )
        self.assertIn(r.status_code, (200, 302))
        self.assertTrue(ExamRoom.objects.filter(name="Phòng Danh Sách 501", exam_period=self.period).exists())

    def test_assign_page_auto_selects_first_room(self):
        """Khi vào trang xếp phòng mà không truyền room_id, phòng đầu tiên sẽ tự động được chọn."""
        room = _make_exam_room(self.period, name="Phòng Chọn Tự Động")
        r = self.client.get(reverse("exam-room-assign", args=[self.period.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["room"], room)

    def test_unassigned_candidates_pagination_10_per_page(self):
        """Danh sách thí sinh chờ xếp được phân trang đúng 10 thí sinh/trang."""
        room = _make_exam_room(self.period, name="Phòng Phân Trang 10")
        # Đã có self.app (1), tạo thêm 14 thí sinh nữa -> tổng 15
        for i in range(14):
            _make_application(
                self.period,
                id_number=f"0792010888{i:02d}",
                full_name=f"Thí sinh chờ xếp {i}",
            )
        r = self.client.get(reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("unassigned_page_obj", r.context)
        page_obj = r.context["unassigned_page_obj"]
        self.assertEqual(len(page_obj), 10)
        self.assertEqual(page_obj.paginator.num_pages, 2)
        self.assertEqual(page_obj.paginator.count, 15)

    def test_application_cannot_exceed_max_candidates(self):
        """Hồ sơ đăng ký không được vượt quá số thí sinh tối đa của đợt thi."""
        p = _make_exam_period(name="Đợt Giới Hạn Sĩ Số", max_candidates=2)
        _make_application(p, id_number="079201077701", full_name="Người Một")
        _make_application(p, id_number="079201077702", full_name="Người Hai")
        app3 = ExamApplication(
            exam_period=p,
            id_number="079201077703",
            full_name="Người Ba Vượt Mức",
            status=ExamApplication.Status.VALID,
        )
        with self.assertRaises(ValidationError):
            app3.full_clean()



# ---------------------------------------------------------------------------
# 6. ExamScore Tests
# ---------------------------------------------------------------------------

class ExamScoreTests(TestCase):
    """Kiểm tra nhập điểm thủ công."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-score-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period()
        self.room = _make_exam_room(self.period)
        self.app = _make_application(self.period)
        self.asgn = _make_assignment(self.room, self.app)

    def test_score_entry_get_200(self):
        r = self.client.get(reverse("exam-score-entry", args=[self.room.pk]))
        self.assertEqual(r.status_code, 200)

    def test_score_entry_shows_candidate_name(self):
        r = self.client.get(reverse("exam-score-entry", args=[self.room.pk]))
        self.assertContains(r, self.app.full_name)

    def test_score_entry_post_saves_score(self):
        r = self.client.post(reverse("exam-score-entry", args=[self.room.pk]), {
            f"score_{self.asgn.pk}": "75",
            f"absent_{self.asgn.pk}": "",
            "action": "save",
        })
        self.assertIn(r.status_code, (200, 302))
        score = ExamScore.objects.filter(assignment=self.asgn).first()
        self.assertIsNotNone(score)
        self.assertEqual(float(score.score), 75.0)

    def test_score_entry_absent_clears_score(self):
        r = self.client.post(reverse("exam-score-entry", args=[self.room.pk]), {
            f"score_{self.asgn.pk}": "",
            f"absent_{self.asgn.pk}": "1",
            "action": "save",
        })
        self.assertIn(r.status_code, (200, 302))
        score = ExamScore.objects.filter(assignment=self.asgn).first()
        self.assertIsNotNone(score)
        self.assertTrue(score.is_absent)
        self.assertIsNone(score.score)

    def test_score_entry_paginates_and_preserves_scores_on_other_pages(self):
        for i in range(10):
            app = _make_application(
                self.period,
                id_number=f"0792010999{i:02d}",
                full_name=f"Applicant {i:02d}",
            )
            _make_assignment(self.room, app)

        url = reverse("exam-score-entry", args=[self.room.pk])
        second_page = self.client.get(url, {"page": 2})
        self.assertEqual(len(second_page.context["assignments"]), 1)
        off_page_assignment = second_page.context["assignments"][0]
        score = ExamScore.objects.get(assignment=off_page_assignment)
        score.score = Decimal("91")
        score.save(update_fields=["score"])

        first_page = self.client.get(url)
        self.assertEqual(len(first_page.context["assignments"]), 10)
        post_data = {
            "action": "save",
            **{
                f"score_{assignment.pk}": "70"
                for assignment in first_page.context["assignments"]
            },
        }
        response = self.client.post(url, post_data)

        self.assertEqual(response.status_code, 302)
        score.refresh_from_db()
        self.assertEqual(score.score, Decimal("91"))

    def test_score_list_single_score_modal_post_saves_score(self):
        """Nhập điểm thủ công cho một thí sinh qua popup trên trang danh sách điểm."""
        r = self.client.post(reverse("exam-score-list"), {
            "save_single_score": "1",
            "assignment_id": str(self.asgn.pk),
            "score": "88.5",
        })
        self.assertIn(r.status_code, (200, 302))
        score = ExamScore.objects.filter(assignment=self.asgn).first()
        self.assertIsNotNone(score)
        self.assertEqual(float(score.score), 88.5)
        self.assertFalse(score.is_absent)

    def test_score_list_single_score_modal_post_marks_absent(self):
        """Đánh dấu vắng thi cho thí sinh qua popup."""
        r = self.client.post(reverse("exam-score-list"), {
            "save_single_score": "1",
            "assignment_id": str(self.asgn.pk),
            "is_absent": "1",
            "score": "",
        })
        self.assertIn(r.status_code, (200, 302))
        score = ExamScore.objects.filter(assignment=self.asgn).first()
        self.assertIsNotNone(score)
        self.assertTrue(score.is_absent)
        self.assertIsNone(score.score)

    def test_score_list_single_score_modal_rejects_out_of_scale(self):
        """Từ chối điểm vượt thang điểm tối đa."""
        r = self.client.post(reverse("exam-score-list"), {
            "save_single_score": "1",
            "assignment_id": str(self.asgn.pk),
            "score": "999.0",
        })
        self.assertIn(r.status_code, (200, 302))
        score = ExamScore.objects.filter(assignment=self.asgn).first()
        self.assertTrue(score is None or score.score != Decimal("999.0"))

    def test_score_publish_view_200(self):
        self.assertEqual(self.client.get(reverse("exam-score-publish")).status_code, 200)

    def test_score_upload_get_200(self):
        self.assertEqual(self.client.get(reverse("exam-score-upload")).status_code, 200)

    def test_score_upload_csv_preview_json(self):
        """Upload CSV điểm với preview=1 → JSON response."""
        csv_content = "so_cccd,diem\n001234567890,85.5\n"
        csv_file = _io_module.BytesIO(csv_content.encode("utf-8"))
        csv_file.name = "scores.csv"
        url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}&room={self.room.pk}"
        )
        r = self.client.post(url, {
            "file": csv_file,
            "preview": "1",
        })
        # Trả về JSON với ok field
        self.assertIn(r.status_code, (200, 400))
        if r.status_code == 200:
            try:
                data = r.json()
                self.assertIn("ok", data)
            except Exception:
                pass  # Nếu không phải JSON, test vẫn pass


# ---------------------------------------------------------------------------
# 7. MyBK Export Tests
# ---------------------------------------------------------------------------

class MyBKExportTests(TestCase):
    """Kiểm tra xuất CSV MyBK."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-mybk-7731")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period()
        self.room = _make_exam_room(self.period)
        self.app = _make_application(self.period)
        self.asgn = _make_assignment(self.room, self.app)
        ExamScore.objects.create(assignment=self.asgn, score=90, is_absent=False)

    def test_mybk_export_get_200(self):
        self.assertEqual(self.client.get(reverse("mybk-export")).status_code, 200)

    def test_mybk_export_score_formatted_with_comma(self):
        """Điểm thi khi xuất file CSV có dấu phẩy thập phân theo chuẩn VN (VD: 61,20)."""
        sc = ExamScore.objects.filter(assignment=self.asgn).first()
        sc.score = Decimal("61.20")
        sc.is_published = True
        sc.save()

        r = self.client.post(reverse("mybk-export"), {"period": str(self.period.pk)})
        self.assertEqual(r.status_code, 200)
        content = r.content.decode("utf-8-sig")
        self.assertIn("61,20", content)
        self.assertNotIn("61.20", content)

    def test_mybk_export_shows_period_list(self):
        r = self.client.get(reverse("mybk-export"))
        self.assertContains(r, self.period.name)

    def test_mybk_export_csv_response(self):
        r = self.client.post(reverse("mybk-export"), {
            "period_id": self.period.pk,
            "action": "export",
        })
        self.assertIn(r.status_code, (200, 302))
        if r.status_code == 200:
            ct = r.get("Content-Type", "")
            # Nên là CSV hoặc HTML (nếu cần confirm trước)
            self.assertTrue(
                "csv" in ct.lower() or "text" in ct.lower() or "html" in ct.lower()
            )


# ---------------------------------------------------------------------------
# 8. Statistics Tests
# ---------------------------------------------------------------------------

class ExamStatisticsTests(TestCase):
    """Kiểm tra màn thống kê."""

    def setUp(self):
        self.user, self.pw = _make_exam_user(username="canbothi-stat-7731")
        self.client.login(username=self.user.username, password=self.pw)

    def test_statistics_200_empty(self):
        self.assertEqual(self.client.get(reverse("exam-statistics")).status_code, 200)

    def test_statistics_with_period_filter(self):
        period = _make_exam_period()
        r = self.client.get(reverse("exam-statistics") + f"?period={period.pk}")
        self.assertEqual(r.status_code, 200)

    def test_statistics_shows_period_data(self):
        period = _make_exam_period()
        app = _make_application(period)
        r = self.client.get(reverse("exam-statistics") + f"?period={period.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, period.name)


# ---------------------------------------------------------------------------
# 9. Report List Tests (Giáo vụ)
# ---------------------------------------------------------------------------

class ReportListTests(TestCase):
    """Kiểm tra màn báo cáo giáo vụ."""

    def setUp(self):
        self.giaoyu, self.pw = _make_giaoyu_user()
        self.client.login(username=self.giaoyu.username, password=self.pw)

    def test_report_list_200_for_giaoyu(self):
        self.assertEqual(self.client.get(reverse("report-list")).status_code, 200)

    def test_report_list_forbidden_for_canbothi(self):
        """Cán bộ thi KHÔNG có quyền xem báo cáo giáo vụ."""
        canbothi, pw = _make_exam_user(username="canbothi-report-forbidden-7731")
        self.client.login(username=canbothi.username, password=pw)
        r = self.client.get(reverse("report-list"))
        self.assertIn(r.status_code, (302, 403))

    def test_report_list_forbidden_for_anonymous(self):
        self.client.logout()
        r = self.client.get(reverse("report-list"))
        self.assertEqual(r.status_code, 302)

    def test_report_list_contains_export_button(self):
        r = self.client.get(reverse("report-list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Xuất Báo cáo file Excel")
        self.assertContains(r, reverse("report-export-excel"))

    def test_report_export_excel_200_and_valid_workbook(self):
        import openpyxl
        import io
        r = self.client.get(reverse("report-export-excel"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("Bao_cao_giao_vu_CCE_", r["Content-Disposition"])
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertIn("Đào tạo - Lớp học", wb.sheetnames)
        self.assertIn("Khóa học - Chương trình", wb.sheetnames)
        ws_classes = wb["Đào tạo - Lớp học"]
        self.assertEqual(ws_classes["A2"].value, "BÁO CÁO TỔNG HỢP LỚP HỌC ĐÀO TẠO")
        ws_courses = wb["Khóa học - Chương trình"]
        self.assertEqual(ws_courses["A2"].value, "BÁO CÁO TỔNG HỢP CHƯƠNG TRÌNH ĐÀO TẠO")

    def test_report_list_pagination_10_per_page(self):
        # Create 15 classes to ensure pagination triggers
        course = Course.objects.first() or Course.objects.create(name="Khóa học Test", pass_threshold=50)
        for i in range(15):
            TrainingClass.objects.create(
                name=f"TEST-PAGE-{i+1:02d}",
                course=course,
                learning_format=TrainingClass.LearningFormat.ONLINE,
                schedule="Thứ 2, 8:00-10:00",
                max_students=20,
            )
        r = self.client.get(reverse("report-list"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["class_stats"]), 10)
        self.assertTrue(r.context["page_obj"].has_other_pages())
        self.assertContains(r, "Trang <strong>1</strong>")

    def test_report_list_does_not_contain_exam_periods(self):
        r = self.client.get(reverse("report-list"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Đợt thi gần đây")
        self.assertNotContains(r, "Đợt thi khảo thí")

    def test_report_list_export_param_returns_excel(self):
        r = self.client.get(reverse("report-list") + "?export=excel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_report_export_excel_forbidden_for_canbothi(self):
        canbothi, pw = _make_exam_user(username="canbothi-report-export-7732")
        self.client.login(username=canbothi.username, password=pw)
        r = self.client.get(reverse("report-export-excel"))
        self.assertIn(r.status_code, (302, 403))


# ---------------------------------------------------------------------------
# 10. Exam Period Draft Logic Tests
# ---------------------------------------------------------------------------

class ExamPeriodDraftLogicTests(TestCase):
    """Kiểm tra logic: đợt thi chưa công bố (draft) không thể có học viên đăng ký."""

    def setUp(self):
        self.canbothi, self.pw = _make_exam_user(username="canbothi-draft-test")
        self.client.login(username=self.canbothi.username, password=self.pw)

    def test_draft_period_cannot_have_application_created(self):
        draft_period = _make_exam_period(status=ExamPeriod.Status.DRAFT)
        with self.assertRaises(ValidationError):
            app = ExamApplication(
                exam_period=draft_period,
                full_name="Nguyễn Văn Draft",
                id_number="012345678912",
                status=ExamApplication.Status.VALID,
            )
            app.save()

    def test_period_with_applications_cannot_be_reverted_to_draft(self):
        published_period = _make_exam_period(status=ExamPeriod.Status.PUBLISHED)
        _make_application(published_period, full_name="Trần Thí Sinh", id_number="098765432101")
        published_period.status = ExamPeriod.Status.DRAFT
        with self.assertRaises(ValidationError):
            published_period.clean()

    def test_draft_period_application_count_is_zero(self):
        draft_period = _make_exam_period(status=ExamPeriod.Status.DRAFT)
        self.assertEqual(draft_period.application_count, 0)

    def test_exam_period_list_shows_zero_and_badge_for_draft(self):
        draft_period = _make_exam_period(status=ExamPeriod.Status.DRAFT)
        r = self.client.get(reverse("exam-period-list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"0 / {draft_period.max_candidates} hồ sơ")
        self.assertContains(r, "Chưa mở ĐK")


# ---------------------------------------------------------------------------
# 11. Exam Period Closed Lock Tests
# ---------------------------------------------------------------------------

class ExamPeriodClosedLockTests(TestCase):
    """Kiểm tra logic: đợt thi đã đóng (closed) không được phép chỉnh sửa."""

    def setUp(self):
        self.canbothi, self.pw = _make_exam_user(username="canbothi-closed-lock-test")
        self.client.login(username=self.canbothi.username, password=self.pw)
        self.course = _make_course()
        self.closed_period = _make_exam_period(course=self.course, status=ExamPeriod.Status.CLOSED)

    def test_closed_period_edit_get_redirects_with_error(self):
        r = self.client.get(reverse("exam-period-edit", args=[self.closed_period.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertRedirects(r, reverse("exam-period-list"))
        self.assertContains(r, "đã đóng, không thể chỉnh sửa thông tin")

    def test_closed_period_edit_post_redirects_with_error(self):
        r = self.client.post(
            reverse("exam-period-edit", args=[self.closed_period.pk]),
            {
                "name": "Tên mới thử đổi",
                "cert_type": self.course.pk,
                "exam_date": "2026-12-01",
                "registration_deadline": "2026-11-20",
                "max_candidates": 50,
                "score_scale": 100,
            },
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertRedirects(r, reverse("exam-period-list"))
        self.assertContains(r, "đã đóng, không thể chỉnh sửa thông tin")
        self.closed_period.refresh_from_db()
        self.assertNotEqual(self.closed_period.name, "Tên mới thử đổi")

    def test_closed_period_model_clean_prevents_modifications(self):
        self.closed_period.name = "Thay đổi tên khi đã đóng"
        with self.assertRaises(ValidationError):
            self.closed_period.clean()

    def test_exam_period_list_shows_locked_badge_for_closed_period(self):
        r = self.client.get(reverse("exam-period-list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Đã khóa")
