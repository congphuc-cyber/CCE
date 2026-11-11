from io import StringIO

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

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
        self.assertEqual(model_admin.list_per_page, 25)

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
        self.assertContains(response, "stat-cards-lighter-20261111")
        self.assertContains(response, "Quản lý đào tạo")
        self.assertContains(response, "Hồ sơ & Mật khẩu")
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
        self.assertContains(response, "cce-admin-user-wrap")
        self.assertContains(response, "cce-admin-user-role")
        self.assertContains(response, "Giáo vụ")
        self.assertContains(response, FEATURE_ICONS["courses"])
        self.assertContains(response, "Quản lý khảo thí")
        self.assertContains(response, "roles-delete-ui-20261111")
        self.assertContains(response, "Hồ sơ & Mật khẩu")
        self.assertContains(response, 'class="cce-admin-header-logout"')
        self.assertContains(response, 'class="cce-logout-button"')
        self.assertContains(response, 'class="cce-admin-search-actions"')
        self.assertContains(response, 'class="cce-admin-filter-add"')
        self.assertContains(response, "Danh sách khóa học")
        self.assertContains(response, "Ngưỡng đạt")
        page = response.content.decode()
        self.assertLess(page.index("cce-admin-user-wrap"), page.index("nav-sidebar-apps"))
        self.assertLess(page.index('id="changelist-search"'), page.index('class="addlink'))
        self.assertNotIn('title="Thêm vào Khóa học"', page[:page.index('id="changelist-search"')])

        response = self.client.get(reverse("admin:training_trainingclass_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ATD-01")
        self.assertContains(response, "Xem")
        self.assertContains(response, "training/css/cce-unfold.css")
        self.assertFalse(self.education_user.is_staff)

    def test_admin_dashboard_sidebar_section_is_always_visible(self):
        sections = settings.UNFOLD["SIDEBAR"]["navigation"]
        self.assertTrue(sections)
        self.assertEqual(sections[0]["title"], "Bảng điều khiển")
        self.assertFalse(sections[0]["collapsible"])
        self.assertTrue(all(section["collapsible"] for section in sections[1:-1]))
        self.assertEqual(sections[3]["title"], "Quản lý khảo thí")
        self.assertEqual(sections[-1]["title"], "")
        self.assertFalse(sections[-1]["collapsible"])

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
            old_values={"name": "Tên cũ"},
            new_values={"name": "Tên mới"},
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
        self.assertContains(response, '&quot;name&quot;: &quot;Tên cũ&quot;')
        self.assertContains(response, '&quot;name&quot;: &quot;Tên mới&quot;')

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
        self.assertEqual(Teacher.objects.filter(user__groups=teacher_group).count(), 3)

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
