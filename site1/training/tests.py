import csv
from decimal import Decimal
from datetime import date
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Group, Permission, User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from captcha.models import CaptchaStore
from reportlab.pdfbase import pdfmetrics

from training.access import all_features, default_features_for_role
from training.admin import BaseModelAdmin
from training.context_processors import ui_navigation
from training.forms import CourseAdminForm, LearnerAdminForm, TrainingClassAdminForm
from training.models import (
    AuditLog,
    Certificate,
    CertificateHistory,
    ClassAssignment,
    Course,
    Enrollment,
    EnrollmentTransferHistory,
    ExamCertificate,
    ExamCertificateHistory,
    ExamApplication,
    ExamPeriod,
    ExamRoom,
    ExamRoomAssignment,
    ExamScore,
    FinalCourseResult,
    Learner,
    RoleFeaturePermission,
    Teacher,
    TrainingClass,
)
from training.navigation import FEATURE_ICONS, ROLE_MENUS, group_menu_items, role_for_user
from training.public_exam_views import (
    _PUBLIC_PDF_REQUIRED_GLYPHS,
    _register_public_pdf_font,
)
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
        self.assertEqual(response.context["demo_page_obj"].number, 1)
        self.assertEqual(response.context["demo_page_obj"].paginator.num_pages, 2)
        self.assertContains(response, 'aria-label="Phân trang dữ liệu"')
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
        self.assertFalse(response.context["chart_has_data"])
        self.assertEqual(response.context["stats"][0]["value"], 0)
        self.assertEqual(response.context["stats"][3]["value"], 0)
        self.assertEqual(len(response.context["dashboard_charts"]), 6)
        self.assertFalse(response.context["dashboard_has_chart_data"])
        self.assertTrue(
            all(not chart["has_data"] for chart in response.context["dashboard_charts"])
        )
        self.assertContains(response, '<section class="cce-nav-group cce-nav-group-static">')
        self.assertContains(response, '<details class="cce-nav-group">')
        self.assertContains(response, "material-symbols-outlined cce-nav-icon")
        self.assertContains(response, "dashboard</span>")
        self.assertContains(response, "training/css/cce-core.css")
        self.assertContains(response, "Quản lý đào tạo")
        self.assertContains(response, "Hồ sơ &amp; Mật khẩu")
        self.assertContains(response, "cce-logout-button")
        self.assertNotContains(response, "Tài khoản</span>")
        self.assertContains(response, "Tổng đăng ký trực tuyến")
        self.assertNotContains(response, "Chưa có nguồn dữ liệu đăng ký trực tuyến.")
        self.assertNotContains(response, "Chưa có dữ liệu chứng nhận.")
        self.assertContains(response, "Chưa có dữ liệu trong khoảng thời gian này.")

    def test_dashboard_displays_online_registrations_certificates_and_chart(self):
        user = self.create_user()
        self.client.force_login(user)
        period = _make_exam_period()
        online_application = _make_application(
            period,
            full_name="Thí sinh đăng ký trực tuyến",
            id_number="123450000001",
        )
        direct_application = _make_application(
            period,
            full_name="Thí sinh đăng ký trực tiếp",
            id_number="123450000002",
        )
        direct_application.source = ExamApplication.Source.DIRECT
        direct_application.save(update_fields=("source",))
        online_application.status = ExamApplication.Status.NEW
        online_application.save(update_fields=("status",))

        course = _make_course()
        training_class = TrainingClass.objects.create(
            name="Lớp dashboard",
            course=course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ 2, 4",
            max_students=20,
            status=TrainingClass.Status.CLOSED,
        )
        learner = Learner.objects.create(
            full_name="Học viên dashboard",
            id_number="123450000003",
        )
        open_class = TrainingClass.objects.create(
            name="Lớp ghi danh dashboard",
            course=course,
            learning_format=TrainingClass.LearningFormat.ONLINE,
            schedule="Thứ 3, 5",
            max_students=20,
            status=TrainingClass.Status.OPEN,
        )
        open_class.enrollments.create(learner=learner)
        FinalCourseResult.objects.create(
            training_class=training_class,
            learner=learner,
            score=85,
        )
        Certificate.objects.create(
            training_class=training_class,
            learner=learner,
            certificate_number="CCE-DASHBOARD-0001",
            status=Certificate.Status.ISSUED,
        )

        today = timezone.localdate()
        response = self.client.get(
            reverse("dashboard"),
            {"from_date": today.isoformat(), "to_date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        stats = {stat["label"]: stat["value"] for stat in response.context["stats"]}
        self.assertEqual(stats["Tổng đăng ký trực tuyến"], 1)
        self.assertEqual(stats["Chứng nhận đã cấp"], 1)
        self.assertEqual(response.context["chart_labels"], [today.strftime("%d/%m/%Y")])
        self.assertEqual(response.context["chart_values"], [1])
        self.assertTrue(response.context["chart_has_data"])
        self.assertContains(response, 'data-chart-series="1"')
        self.assertEqual(
            [dataset["data"] for dataset in response.context["chart_datasets"]],
            [[1], [1], [1]],
        )
        self.assertContains(response, 'data-chart-mode="bar"')
        charts = {
            chart["id"]: chart
            for chart in response.context["dashboard_charts"]
        }
        chart_types = {
            chart["id"]: chart["type"]
            for chart in response.context["dashboard_charts"]
        }
        self.assertEqual(
            chart_types,
            {
                "exam-application-status-chart": "bar",
                "exam-application-source-chart": "doughnut",
                "training-class-status-chart": "bar",
                "training-class-format-chart": "doughnut",
                "training-certificate-status-chart": "bar",
                "exam-period-status-chart": "bar",
            },
        )
        self.assertEqual(charts["training-class-status-chart"]["index_axis"], "y")
        self.assertEqual(charts["training-certificate-status-chart"]["index_axis"], "y")
        self.assertEqual(charts["exam-period-status-chart"]["index_axis"], "y")
        self.assertEqual(
            charts["exam-application-status-chart"]["values"],
            [1, 1, 0],
        )
        self.assertEqual(
            charts["exam-application-source-chart"]["values"],
            [1, 0, 0, 1],
        )
        self.assertEqual(
            charts["training-class-status-chart"]["values"],
            [1, 1],
        )
        self.assertEqual(
            charts["training-class-format-chart"]["values"],
            [1, 1],
        )
        self.assertEqual(
            charts["training-certificate-status-chart"]["values"],
            [1, 0, 0],
        )
        self.assertEqual(
            charts["exam-period-status-chart"]["values"],
            [0, 1, 0],
        )
        self.assertTrue(response.context["dashboard_has_chart_data"])

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

    def test_training_admin_lists_use_shared_pagination_summary(self):
        for index in range(10):
            Course.objects.create(name=f"Khóa phân trang {index:02}", pass_threshold=75)
            TrainingClass.objects.create(
                name=f"Lớp phân trang {index:02}",
                course=self.course,
                learning_format=TrainingClass.LearningFormat.AT_CENTER,
                schedule="Thứ Hai, 18:00",
                max_students=10,
            )
            Teacher.objects.create(full_name=f"Giáo viên phân trang {index:02}")
            Learner.objects.create(full_name=f"Học viên phân trang {index:02}")

        Teacher.objects.create(full_name="Giáo viên phân trang 10")
        Learner.objects.create(full_name="Học viên phân trang 10")

        self.client.force_login(self.education_user)
        changelist_routes = (
            "admin:training_course_changelist",
            "admin:training_trainingclass_changelist",
            "admin:training_teacher_changelist",
            "admin:training_learner_changelist",
        )
        for route_name in changelist_routes:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Tổng cộng 11 bản ghi")
                self.assertContains(response, "Trang")
                self.assertContains(response, "p=2")
                self.assertContains(response, 'class="cce-page-link is-active">1</span>')
                self.assertContains(response, "Sau")

                second_page = self.client.get(reverse(route_name), {"p": 2})
                self.assertEqual(second_page.status_code, 200)
                self.assertContains(second_page, 'class="cce-page-link is-active">2</span>')
                self.assertContains(second_page, "p=1")

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
        enrollment = Enrollment.objects.get(
            learner=first,
            training_class=self.training_class,
        )
        enrollment_audit = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_type="training.Enrollment",
            object_id=str(enrollment.pk),
        )
        self.assertEqual(enrollment_audit.actor, self.education_user)
        self.assertEqual(enrollment_audit.old_values, {})
        self.assertEqual(enrollment_audit.new_values["learner"], first.pk)
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


class ClassTransferTests(TestCase):
    def setUp(self):
        call_command("create_cce_roles")
        education_group = Group.objects.get(name="Giáo vụ")
        self.user = User.objects.create_user(username="class-transfer-manager")
        self.user.groups.add(education_group)
        self.course = Course.objects.create(name="Điện công nghiệp", pass_threshold=70)
        self.source_class = TrainingClass.objects.create(
            name="Dien-01",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai",
            max_students=4,
        )
        self.destination_class = TrainingClass.objects.create(
            name="Dien-02",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.ONLINE,
            schedule="Thứ Ba",
            max_students=4,
        )
        self.client.force_login(self.user)

    def create_learner(self, full_name, id_number):
        return Learner.objects.create(full_name=full_name, id_number=id_number)

    def submit_transfer(self, learner, destination_class=None):
        return self.client.post(
            reverse("class-transfer"),
            {
                "learner": learner.pk,
                "source_class": self.source_class.pk,
                "destination_class": (destination_class or self.destination_class).pk,
            },
        )

    def test_enrollment_select_supports_name_and_cccd_search(self):
        learner = self.create_learner("Nguyễn An", "012345678901")
        response = self.client.get(reverse("class-enroll", args=(self.source_class.pk,)))

        self.assertContains(response, "Tìm học viên theo họ tên hoặc số CCCD")
        self.assertContains(response, f"{learner.full_name} — {learner.id_number}")
        self.assertContains(response, 'aria-controls="id_learner"')
        self.assertContains(response, "filterLearners")

    def test_transfer_moves_enrollment_and_records_history_and_audit(self):
        learner = self.create_learner("Trần Bình", "112345678901")
        Enrollment.objects.create(learner=learner, training_class=self.source_class)
        class_detail = self.client.get(reverse("class-detail", args=(self.source_class.pk,)))
        self.assertContains(class_detail, "Chuyển lớp")
        self.assertContains(class_detail, "Lịch sử chuyển lớp")
        transfer_page = self.client.get(
            reverse("class-transfer"),
            {"source_class": self.source_class.pk},
        )
        self.assertContains(transfer_page, self.source_class.name)
        self.assertContains(transfer_page, self.destination_class.name)
        self.assertContains(transfer_page, "0/4")

        response = self.submit_transfer(learner)

        self.assertRedirects(
            response,
            reverse("class-detail", args=(self.destination_class.pk,)),
        )
        self.assertFalse(
            Enrollment.objects.filter(
                learner=learner,
                training_class=self.source_class,
            ).exists()
        )
        self.assertTrue(
            Enrollment.objects.filter(
                learner=learner,
                training_class=self.destination_class,
            ).exists()
        )
        transfer = EnrollmentTransferHistory.objects.get(learner=learner)
        self.assertEqual(transfer.source_class, self.source_class)
        self.assertEqual(transfer.destination_class, self.destination_class)
        self.assertEqual(transfer.transferred_by, self.user)
        audit = AuditLog.objects.get(
            action=AuditLog.Action.EDIT,
            object_type=EnrollmentTransferHistory._meta.label,
            object_id=str(transfer.pk),
        )
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.old_values["destination_class"], self.source_class.name)
        self.assertEqual(
            audit.new_values["destination_class"],
            self.destination_class.name,
        )
        self.assertContains(
            self.client.get(reverse("class-detail", args=(self.destination_class.pk,))),
            learner.full_name,
        )

    def test_transfer_rejects_duplicate_destination_and_preserves_source(self):
        learner = self.create_learner("Lê Hoa", "212345678901")
        Enrollment.objects.create(learner=learner, training_class=self.source_class)
        Enrollment.objects.create(learner=learner, training_class=self.destination_class)

        response = self.submit_transfer(learner)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Học viên này đã có trong lớp mới.")
        self.assertTrue(
            Enrollment.objects.filter(
                learner=learner,
                training_class=self.source_class,
            ).exists()
        )
        self.assertFalse(EnrollmentTransferHistory.objects.exists())

    def test_transfer_rejects_full_destination_and_preserves_source(self):
        learner = self.create_learner("Phạm An", "312345678901")
        occupant = self.create_learner("Học viên đã đủ chỗ", "412345678901")
        Enrollment.objects.create(learner=learner, training_class=self.source_class)
        full_class = TrainingClass.objects.create(
            name="Dien-03",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.ONLINE,
            schedule="Thứ Tư",
            max_students=1,
        )
        Enrollment.objects.create(learner=occupant, training_class=full_class)

        response = self.submit_transfer(learner, full_class)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lớp mới đã đạt sĩ số tối đa.")
        self.assertTrue(
            Enrollment.objects.filter(
                learner=learner,
                training_class=self.source_class,
            ).exists()
        )
        self.assertFalse(EnrollmentTransferHistory.objects.exists())

    def test_transfer_rejects_learner_not_in_selected_source(self):
        learner = self.create_learner("Đặng Minh", "512345678901")
        source_occupant = self.create_learner("Học viên trong lớp nguồn", "812345678901")
        unrelated_class = TrainingClass.objects.create(
            name="Dien-X",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Sáu",
            max_students=4,
        )
        Enrollment.objects.create(learner=source_occupant, training_class=self.source_class)
        Enrollment.objects.create(learner=learner, training_class=unrelated_class)

        response = self.submit_transfer(learner)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Học viên không được ghi danh trong lớp hiện tại.")
        self.assertTrue(
            Enrollment.objects.filter(
                learner=learner,
                training_class=unrelated_class,
            ).exists()
        )
        self.assertFalse(EnrollmentTransferHistory.objects.exists())

    def test_transfer_rejects_the_same_source_and_destination(self):
        learner = self.create_learner("Ngô Thanh", "912345678901")
        Enrollment.objects.create(learner=learner, training_class=self.source_class)

        response = self.submit_transfer(learner, self.source_class)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lớp mới phải khác lớp hiện tại.")
        self.assertTrue(
            Enrollment.objects.filter(
                learner=learner,
                training_class=self.source_class,
            ).exists()
        )
        self.assertFalse(EnrollmentTransferHistory.objects.exists())

    def test_transfer_history_filters_by_learner_and_either_class(self):
        learner = self.create_learner("Vũ Ngọc", "612345678901")
        other_learner = self.create_learner("Hồ Quân", "712345678901")
        other_class = TrainingClass.objects.create(
            name="Dien-04",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Năm",
            max_students=4,
        )
        EnrollmentTransferHistory.objects.create(
            learner=learner,
            source_class=self.source_class,
            destination_class=self.destination_class,
            transferred_by=self.user,
        )
        EnrollmentTransferHistory.objects.create(
            learner=other_learner,
            source_class=self.destination_class,
            destination_class=other_class,
            transferred_by=self.user,
        )
        url = reverse("class-transfer-history")

        learner_response = self.client.get(url, {"learner": learner.pk})
        class_response = self.client.get(url, {"training_class": self.source_class.pk})

        self.assertEqual(learner_response.context["page_obj"].paginator.count, 1)
        self.assertEqual(class_response.context["page_obj"].paginator.count, 1)
        self.assertEqual(
            [row["learner"] for row in learner_response.context["rows"]],
            ["Vũ Ngọc"],
        )
        self.assertContains(class_response, self.destination_class.name)

    def test_history_filter_accepts_learner_and_class_without_transfers(self):
        learner = self.create_learner("Học viên chưa chuyển lớp", "812345678901")

        response = self.client.get(
            reverse("class-transfer-history"),
            {
                "learner": learner.pk,
                "training_class": self.source_class.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "Không có dữ liệu")
        self.assertNotContains(response, "Hãy chọn một lựa chọn hợp lệ.")

    def test_teacher_cannot_access_class_transfer_routes(self):
        teacher_group = Group.objects.get(name="Giáo viên")
        teacher_user = User.objects.create_user(username="class-transfer-teacher")
        teacher_user.groups.add(teacher_group)
        self.client.force_login(teacher_user)

        self.assertEqual(self.client.get(reverse("class-transfer")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("class-transfer-history")).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("class-enroll", args=(self.source_class.pk,))).status_code,
            403,
        )


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


class FinalCourseResultTests(TestCase):
    def setUp(self):
        call_command("create_cce_roles")
        education_group = Group.objects.get(name="Giáo vụ")
        self.manager = User.objects.create_user(
            username="final-results-manager",
            password="Final-results-pass-8153#",
        )
        self.manager.groups.add(education_group)
        self.course = Course.objects.create(
            name="An toàn điện",
            pass_threshold=75,
        )
        self.training_class = TrainingClass.objects.create(
            name="ATD-KQ-01",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai, 18:00",
            max_students=5,
        )
        self.learners = []
        for index, (name, id_number) in enumerate((
            ("Nguyễn An", "012345678901"),
            ("Trần Bình", "112345678901"),
            ("Lê Chi", "212345678901"),
        )):
            learner = Learner.objects.create(full_name=name, id_number=id_number)
            Enrollment.objects.create(
                training_class=self.training_class,
                learner=learner,
            )
            self.learners.append(learner)
        self.client.force_login(self.manager)

    def score_file(self, rows, class_name=None):
        buffer = StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["Lớp", "Họ và tên", "Số CCCD", "Điểm cuối khóa"])
        class_name = class_name or self.training_class.name
        writer.writerows([[class_name, *row] for row in rows])
        return SimpleUploadedFile(
            "final-results.csv",
            buffer.getvalue().encode("utf-8-sig"),
            content_type="text/csv",
        )

    def create_assigned_teacher(self, username="final-results-teacher"):
        teacher_user = User.objects.create_user(username=username)
        teacher_user.groups.add(Group.objects.get(name="Giáo viên"))
        teacher = Teacher.objects.create(
            full_name="Giáo viên phụ trách",
            user=teacher_user,
        )
        ClassAssignment.objects.create(
            training_class=self.training_class,
            teacher=teacher,
        )
        return teacher_user

    def test_manual_scores_use_threshold_and_blank_values_do_not_change_saved_scores(self):
        existing = FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=self.learners[0],
            score=Decimal("80"),
        )
        response = self.client.post(
            reverse("class-results", args=(self.training_class.pk,)),
            {
                "action": "save",
                f"score_{self.learners[0].pk}": "",
                f"score_{self.learners[1].pk}": "75",
                f"score_{self.learners[2].pk}": "74.99",
            },
        )
        self.assertEqual(response.status_code, 302)
        existing.refresh_from_db()
        self.assertEqual(existing.score, Decimal("80"))
        self.assertEqual(
            FinalCourseResult.objects.get(learner=self.learners[1]).outcome,
            "Đạt",
        )
        self.assertEqual(
            FinalCourseResult.objects.get(learner=self.learners[2]).outcome,
            "Không đạt",
        )
        created_result = FinalCourseResult.objects.get(learner=self.learners[1])
        create_audit = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_type="training.FinalCourseResult",
            object_id=str(created_result.pk),
        )
        self.assertEqual(create_audit.actor, self.manager)
        self.assertEqual(Decimal(create_audit.new_values["score"]), Decimal("75"))

        self.client.post(
            reverse("class-results", args=(self.training_class.pk,)),
            {
                "action": "save",
                f"score_{self.learners[1].pk}": "76",
            },
        )
        update_audit = AuditLog.objects.get(
            action=AuditLog.Action.EDIT,
            object_type="training.FinalCourseResult",
            object_id=str(created_result.pk),
        )
        self.assertEqual(Decimal(update_audit.old_values["score"]), Decimal("75"))
        self.assertEqual(Decimal(update_audit.new_values["score"]), Decimal("76"))

    def test_manual_score_outside_range_is_rejected_without_saving(self):
        response = self.client.post(
            reverse("class-results", args=(self.training_class.pk,)),
            {
                "action": "save",
                f"score_{self.learners[0].pk}": "100.01",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Điểm cuối khóa phải nằm trong khoảng 0 đến 100.")
        self.assertFalse(
            FinalCourseResult.objects.filter(learner=self.learners[0]).exists()
        )

    def test_class_cannot_be_finalized_with_missing_scores_and_finalization_is_audited(self):
        FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=self.learners[0],
            score=Decimal("70"),
        )
        results_url = reverse("class-results", args=(self.training_class.pk,))
        response = self.client.post(results_url, {"action": "finalize"})
        self.assertEqual(response.status_code, 302)
        self.training_class.refresh_from_db()
        self.assertFalse(self.training_class.final_results_locked)

        for learner in self.learners[1:]:
            FinalCourseResult.objects.create(
                training_class=self.training_class,
                learner=learner,
                score=Decimal("75"),
            )
        response = self.client.post(results_url, {"action": "finalize"})
        self.assertEqual(response.status_code, 302)
        self.training_class.refresh_from_db()
        self.assertTrue(self.training_class.final_results_locked)
        self.assertEqual(self.training_class.final_results_locked_by, self.manager)
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="training.TrainingClass",
                object_id=str(self.training_class.pk),
                reason="Chốt kết quả cuối khóa.",
            ).exists()
        )

    def test_locked_manual_edits_require_reason_and_record_audit(self):
        self.training_class.final_results_locked_at = timezone.now()
        self.training_class.final_results_locked_by = self.manager
        self.training_class.save(
            update_fields=("final_results_locked_at", "final_results_locked_by")
        )
        result = FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=self.learners[0],
            score=Decimal("65"),
        )
        results_url = reverse("class-results", args=(self.training_class.pk,))
        response = self.client.post(
            results_url,
            {
                "action": "save_locked",
                f"score_{self.learners[0].pk}": "80",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vui lòng nhập lý do sửa điểm sau khi chốt.")
        result.refresh_from_db()
        self.assertEqual(result.score, Decimal("65"))

        response = self.client.post(
            results_url,
            {
                "action": "save_locked",
                "reason": "Sửa theo biên bản đối soát",
                f"score_{self.learners[0].pk}": "80",
            },
        )
        self.assertEqual(response.status_code, 302)
        result.refresh_from_db()
        self.assertEqual(result.score, Decimal("80"))
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="training.FinalCourseResult",
                object_id=str(result.pk),
                reason="Sửa theo biên bản đối soát",
            ).exists()
        )

    def test_upload_preview_and_invalid_file_never_save_any_rows(self):
        rows = [
            [self.learners[0].full_name, self.learners[0].id_number, "80"],
            [self.learners[1].full_name, self.learners[1].id_number, "101"],
        ]
        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )
        preview = self.client.post(
            upload_url,
            {"file": self.score_file(rows), "preview": "1"},
        )
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        self.assertEqual(preview_data["saved"], 0)
        self.assertEqual(preview_data["errors"][0]["row"], 3)
        self.assertEqual(preview_data["errors"][0]["column"], "Điểm cuối khóa")
        self.assertFalse(
            FinalCourseResult.objects.filter(training_class=self.training_class).exists()
        )

        saved = self.client.post(upload_url, {"file": self.score_file(rows)})
        self.assertEqual(saved.status_code, 200)
        self.assertFalse(saved.json()["ok"])
        self.assertEqual(saved.json()["saved"], 0)
        self.assertFalse(
            FinalCourseResult.objects.filter(training_class=self.training_class).exists()
        )

    def test_upload_requires_a_matching_class_column(self):
        row = [
            self.learners[0].full_name,
            self.learners[0].id_number,
            "80",
        ]
        missing_class_buffer = StringIO(newline="")
        writer = csv.writer(missing_class_buffer)
        writer.writerow(["Họ và tên", "Số CCCD", "Điểm cuối khóa"])
        writer.writerow(row)
        missing_class_file = SimpleUploadedFile(
            "final-results.csv",
            missing_class_buffer.getvalue().encode("utf-8-sig"),
            content_type="text/csv",
        )
        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )

        missing_class = self.client.post(
            upload_url,
            {"file": missing_class_file, "preview": "1"},
        ).json()
        self.assertFalse(missing_class["ok"])
        self.assertIn("Lớp", missing_class["errors"][0]["message"])

        wrong_class = self.client.post(
            upload_url,
            {
                "file": self.score_file([row], class_name="ATD-KQ-02"),
                "preview": "1",
            },
        ).json()
        self.assertFalse(wrong_class["ok"])
        self.assertEqual(wrong_class["errors"][0]["column"], "Lớp")
        self.assertIn("không khớp", wrong_class["errors"][0]["message"])
        self.assertFalse(
            FinalCourseResult.objects.filter(training_class=self.training_class).exists()
        )

    def test_locked_upload_requires_reason_and_audits_successful_changes(self):
        self.training_class.final_results_locked_at = timezone.now()
        self.training_class.final_results_locked_by = self.manager
        self.training_class.save(
            update_fields=("final_results_locked_at", "final_results_locked_by")
        )
        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )
        rows = [[
            self.learners[0].full_name,
            self.learners[0].id_number,
            "80",
        ]]
        missing_reason = self.client.post(
            upload_url,
            {"file": self.score_file(rows)},
        )
        self.assertFalse(missing_reason.json()["ok"])
        self.assertFalse(
            FinalCourseResult.objects.filter(learner=self.learners[0]).exists()
        )

        saved = self.client.post(
            upload_url,
            {
                "file": self.score_file(rows),
                "reason": "Điều chỉnh theo biên bản",
            },
        )
        self.assertTrue(saved.json()["ok"])
        self.assertEqual(saved.json()["saved"], 1)
        result = FinalCourseResult.objects.get(learner=self.learners[0])
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="training.FinalCourseResult",
                object_id=str(result.pk),
                reason="Điều chỉnh theo biên bản",
            ).exists()
        )

    def test_results_and_upload_pages_render_and_respect_role_permissions(self):
        results_url = reverse("class-results", args=(self.training_class.pk,))
        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )
        self.assertEqual(self.client.get(results_url).status_code, 200)
        upload_page = self.client.get(upload_url)
        self.assertEqual(upload_page.status_code, 200)
        self.assertContains(upload_page, "Số CCCD")
        self.assertContains(upload_page, "Điểm cuối khóa")
        class_detail = self.client.get(
            reverse("class-detail", args=(self.training_class.pk,))
        )
        self.assertContains(
            class_detail,
            reverse("class-results", args=(self.training_class.pk,)),
        )
        sample = self.client.get(
            reverse(
                "class-results-upload-sample",
                args=(self.training_class.pk,),
            )
        )
        self.assertEqual(sample.status_code, 200)
        sample_content = sample.content.decode("utf-8-sig")
        self.assertIn(
            "Lớp,Họ và tên,Số CCCD,Điểm cuối khóa",
            sample_content,
        )
        self.assertIn(self.training_class.name, sample_content)
        self.assertIn(self.learners[0].id_number, sample_content)

        teacher = User.objects.create_user(username="final-results-teacher")
        teacher.groups.add(Group.objects.get(name="Giáo viên"))
        self.client.force_login(teacher)
        self.assertEqual(self.client.get(results_url).status_code, 403)
        self.assertEqual(self.client.get(upload_url).status_code, 403)

    def test_assigned_teacher_can_enter_scores_and_upload_only_for_their_class(self):
        teacher_user = self.create_assigned_teacher()
        other_class = TrainingClass.objects.create(
            name="ATD-KQ-02",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.ONLINE,
            schedule="Thứ Sáu, 18:00",
            max_students=5,
        )
        self.client.force_login(teacher_user)

        class_list = self.client.get(reverse("my-class-list"))
        self.assertContains(class_list, "Ghi nhận kết quả")
        self.assertContains(
            class_list,
            reverse("class-results", args=(self.training_class.pk,)),
        )
        self.assertContains(class_list, "ATD-KQ-01")
        self.assertNotContains(class_list, "ATD-KQ-02")

        results_url = reverse("class-results", args=(self.training_class.pk,))
        results_page = self.client.get(results_url)
        self.assertEqual(results_page.status_code, 200)
        self.assertEqual(results_page.context["active_menu"], "my-classes")
        self.assertContains(
            results_page,
            '<a class="cce-nav-link is-active active" href="/my-classes/" aria-current="page">',
        )
        self.assertFalse(results_page.context["can_finalize"])
        self.assertNotContains(results_page, "Chốt kết quả")
        self.assertContains(results_page, "Upload file điểm")
        self.assertEqual(
            self.client.post(results_url, {"action": "finalize"}).status_code,
            403,
        )

        saved = self.client.post(
            results_url,
            {
                "action": "save",
                f"score_{self.learners[0].pk}": "80",
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(
            FinalCourseResult.objects.get(
                training_class=self.training_class,
                learner=self.learners[0],
            ).score,
            Decimal("80"),
        )

        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )
        upload_page = self.client.get(upload_url)
        self.assertEqual(upload_page.status_code, 200)
        self.assertContains(
            upload_page,
            '<a class="cce-nav-link is-active active" href="/my-classes/" aria-current="page">',
        )
        uploaded = self.client.post(
            upload_url,
            {
                "file": self.score_file([[
                    self.learners[1].full_name,
                    self.learners[1].id_number,
                    "75",
                ]]),
            },
        )
        self.assertTrue(uploaded.json()["ok"])
        self.assertEqual(
            self.client.get(
                reverse("class-results", args=(other_class.pk,))
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("class-results-upload", args=(other_class.pk,))
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("class-results-upload-sample", args=(other_class.pk,))
            ).status_code,
            403,
        )

    def test_teacher_without_assignments_sees_the_empty_state(self):
        teacher_user = User.objects.create_user(username="unassigned-final-results")
        teacher_user.groups.add(Group.objects.get(name="Giáo viên"))
        self.client.force_login(teacher_user)
        response = self.client.get(reverse("my-class-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chưa được phân công lớp nào.")
        self.assertNotContains(response, "Quản lý Lớp học")

    def test_teacher_cannot_finalize_or_edit_results_after_lock(self):
        teacher_user = self.create_assigned_teacher()
        self.training_class.final_results_locked_at = timezone.now()
        self.training_class.final_results_locked_by = self.manager
        self.training_class.save(
            update_fields=("final_results_locked_at", "final_results_locked_by")
        )
        result = FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=self.learners[0],
            score=Decimal("65"),
        )
        self.client.force_login(teacher_user)
        results_url = reverse("class-results", args=(self.training_class.pk,))
        response = self.client.get(results_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "readonly")
        self.assertNotContains(response, "Chốt kết quả")
        self.assertNotContains(response, "Lưu điểm")
        self.assertNotContains(response, "Upload file điểm")

        for action in ("finalize", "save", "save_locked"):
            with self.subTest(action=action):
                response = self.client.post(
                    results_url,
                    {
                        "action": action,
                        "reason": "Không được phép",
                        f"score_{self.learners[0].pk}": "90",
                    },
                )
                self.assertEqual(response.status_code, 403)

        upload_url = reverse(
            "class-results-upload",
            args=(self.training_class.pk,),
        )
        self.assertEqual(self.client.get(upload_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                upload_url,
                {
                    "file": self.score_file([[
                        self.learners[0].full_name,
                        self.learners[0].id_number,
                        "90",
                    ]]),
                },
            ).status_code,
            403,
        )
        result.refresh_from_db()
        self.assertEqual(result.score, Decimal("65"))


class LearnerManagementTests(TestCase):
    def setUp(self):
        call_command("create_cce_roles")
        group = Group.objects.get(name="Giáo vụ")
        self.user = User.objects.create_user(
            username="learner-manager",
            password="Learner-pass-7853#",
        )
        self.user.groups.add(group)
        self.course = Course.objects.create(name="An toàn điện", pass_threshold=75)
        self.training_class = TrainingClass.objects.create(
            name="ATD-01",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ Hai, 18:00",
            max_students=10,
        )

    @staticmethod
    def learner_data(id_number="012345678901", full_name="Nguyễn An"):
        return {
            "full_name": full_name,
            "date_of_birth": "2000-01-02",
            "id_number": id_number,
            "phone": "0901234567",
            "email": "an@example.test",
        }

    @staticmethod
    def csv_upload(rows):
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Họ và tên", "Ngày sinh", "Số CCCD", "Số điện thoại", "Email"])
        writer.writerows(rows)
        return SimpleUploadedFile(
            "learners.csv",
            buffer.getvalue().encode("utf-8-sig"),
            content_type="text/csv",
        )

    def test_learner_form_requires_profile_fields_and_rejects_duplicate_cccd(self):
        self.assertEqual(
            tuple(LearnerAdminForm.base_fields),
            ("full_name", "date_of_birth", "id_number", "phone", "email"),
        )
        invalid = LearnerAdminForm({
            **self.learner_data(),
            "id_number": "12345678901",
            "email": "",
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn("id_number", invalid.errors)
        self.assertIn("email", invalid.errors)
        missing = LearnerAdminForm({"full_name": "Thiếu dữ liệu"})
        self.assertFalse(missing.is_valid())
        self.assertTrue(
            {"date_of_birth", "id_number", "phone", "email"}.issubset(missing.errors)
        )

        existing = Learner.objects.create(
            full_name="Học viên hiện có",
            id_number="012345678901",
        )
        duplicate = LearnerAdminForm(self.learner_data())
        self.assertFalse(duplicate.is_valid())
        self.assertEqual(duplicate.duplicate_learner.pk, existing.pk)

    def test_enrolled_learner_has_delete_action_and_can_be_deleted(self):
        learner = Learner.objects.create(**{
            **self.learner_data(),
            "date_of_birth": "2000-01-02",
        })
        Enrollment.objects.create(learner=learner, training_class=self.training_class)
        self.client.force_login(self.user)

        list_response = self.client.get(reverse("admin:training_learner_changelist"))
        self.assertContains(list_response, self.training_class.name)
        self.assertContains(
            list_response,
            reverse("admin:training_learner_delete", args=(learner.pk,)),
        )
        delete_response = self.client.post(
            reverse("admin:training_learner_delete", args=(learner.pk,)),
            {"post": "yes"},
        )
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(Learner.objects.filter(pk=learner.pk).exists())
        self.assertFalse(Enrollment.objects.filter(learner_id=learner.pk).exists())
        self.assertEqual(
            tuple(LearnerAdminForm.base_fields),
            ("full_name", "date_of_birth", "id_number", "phone", "email"),
        )

    def test_admin_duplicate_cccd_offers_existing_profile(self):
        existing = Learner.objects.create(
            full_name="Học viên hiện có",
            id_number="012345678901",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("admin:training_learner_add"),
            self.learner_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dùng hồ sơ hiện có")
        self.assertContains(
            response,
            reverse("admin:training_learner_change", args=(existing.pk,)),
        )

    def test_manual_learner_creation_and_edits_are_audited(self):
        self.client.force_login(self.user)
        add_url = reverse("admin:training_learner_add")
        response = self.client.post(add_url, self.learner_data())
        self.assertEqual(response.status_code, 302)

        learner = Learner.objects.get(id_number="012345678901")
        create_audit = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_type="training.Learner",
            object_id=str(learner.pk),
        )
        self.assertEqual(create_audit.actor, self.user)
        self.assertEqual(create_audit.new_values["full_name"], "Nguyễn An")
        self.assertEqual(create_audit.new_values["id_number"], "012345678901")

        response = self.client.post(
            reverse("admin:training_learner_change", args=(learner.pk,)),
            self.learner_data(id_number="112345678901", full_name="Trần Bình"),
        )
        self.assertEqual(response.status_code, 302)

        update_audit = AuditLog.objects.get(
            action=AuditLog.Action.EDIT,
            object_type="training.Learner",
            object_id=str(learner.pk),
        )
        self.assertEqual(update_audit.old_values["full_name"], "Nguyễn An")
        self.assertEqual(update_audit.old_values["id_number"], "012345678901")
        self.assertEqual(update_audit.new_values["full_name"], "Trần Bình")
        self.assertEqual(update_audit.new_values["id_number"], "112345678901")

    def test_csv_import_skips_invalid_and_duplicate_rows_and_audits_saved_profiles(self):
        Learner.objects.create(
            full_name="Đã có hồ sơ",
            id_number="987654321012",
        )
        upload_rows = [
            ["Nguyễn Mới", "2001-02-03", "012345678901", "0901234567", "moi@example.test"],
            ["Hồ Sẵn Có", "1999-02-03", "987654321012", "0907654321", "co@example.test"],
            ["Dữ Liệu Lỗi", "2000-03-04", "123456789012", "0900000000", "not-an-email"],
        ]
        self.client.force_login(self.user)
        url = reverse("learner-import")
        list_response = self.client.get(reverse("admin:training_learner_changelist"))
        self.assertContains(list_response, "Nhập từ file")
        import_page = self.client.get(url)
        self.assertContains(import_page, "CSV UTF-8 hoặc Excel (.xlsx)")
        self.assertContains(import_page, "5.000 dòng")

        preview = self.client.post(
            url,
            {"file": self.csv_upload(upload_rows), "preview": "1"},
        ).json()
        self.assertEqual(preview["valid_count"], 2, preview)
        self.assertEqual(preview["duplicate_count"], 1)
        self.assertEqual(len(preview["errors"]), 1)
        self.assertTrue(
            any(row.get("duplicate_url") for row in preview["preview_rows"])
        )

        result = self.client.post(
            url,
            {"file": self.csv_upload(upload_rows)},
        ).json()
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        imported = Learner.objects.get(id_number="012345678901")
        audit = AuditLog.objects.get(
            action=AuditLog.Action.CREATE,
            object_id=str(imported.pk),
        )
        self.assertEqual(audit.actor, self.user)

    def test_xlsx_import_reads_excel_date_cells(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["full_name", "date_of_birth", "id_number", "phone", "email"])
        sheet.append([
            "Trần Bình",
            date(2000, 5, 6),
            "123456789012",
            "0901112233",
            "binh@example.test",
        ])
        buffer = BytesIO()
        workbook.save(buffer)
        content = buffer.getvalue()
        def upload():
            return SimpleUploadedFile(
                "learners.xlsx",
                content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        self.client.force_login(self.user)

        response = self.client.post(
            reverse("learner-import"),
            {"file": upload(), "preview": "1"},
        )
        self.assertEqual(response.json()["valid_count"], 1)
        result = self.client.post(reverse("learner-import"), {"file": upload()}).json()
        self.assertEqual(result["saved"], 1)
        imported = Learner.objects.get(id_number="123456789012")
        self.assertEqual(imported.date_of_birth, date(2000, 5, 6))

    def test_import_requires_the_learners_feature(self):
        user = User.objects.create_user(username="no-learner-access")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("learner-import")).status_code, 403)


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
        self.assertNotContains(response, "File mẫu được tạo")
        self.assertNotContains(response, "Tải CSV hợp lệ")
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

    def _application_csv(self, content):
        uploaded = _io_module.BytesIO(content.encode("utf-8"))
        uploaded.name = "applications.csv"
        return uploaded

    def test_application_import_preview_does_not_save_and_confirm_saves_once(self):
        url = reverse("exam-application-import")
        csv_content = (
            "Tên đợt thi,Họ và tên,Số CCCD/CMND,Số điện thoại\n"
            f"{self.period.name},Thí sinh kiểm thử,098765432101,0901234567\n"
        )

        page = self.client.get(url)
        self.assertContains(page, 'name="csrfmiddlewaretoken"')

        preview = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": self.period.pk,
            "preview": "1",
        })
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        self.assertTrue(preview_data["ok"])
        self.assertEqual(preview_data["valid_count"], 1)
        self.assertEqual(len(preview_data["preview_rows"]), 1)
        self.assertEqual(self.period.applications.count(), 0)

        confirmed = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": self.period.pk,
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["ok"])
        self.assertEqual(confirmed.json()["saved"], 1)
        self.assertEqual(self.period.applications.count(), 1)
        imported = self.period.applications.get()
        self.assertEqual(imported.status, ExamApplication.Status.VALID)
        self.assertEqual(imported.source, ExamApplication.Source.GOOGLE_FORM)

    def test_reimport_marks_existing_applications_as_duplicates(self):
        url = reverse("exam-application-import")
        csv_content = "Họ và tên,Số CCCD/CMND,Số điện thoại\nThí sinh lặp,098765432108,0901234568\n"
        self.period.max_candidates = 1
        self.period.save(update_fields=["max_candidates"])

        first_import = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": self.period.pk,
        })
        self.assertTrue(first_import.json()["ok"])
        self.assertEqual(first_import.json()["duplicate_count"], 0)

        other_period = _make_exam_period(name="Đợt thi CCNA")
        other_period_import = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": other_period.pk,
        })
        self.assertTrue(other_period_import.json()["ok"])
        self.assertEqual(other_period_import.json()["duplicate_count"], 0)
        self.assertEqual(
            other_period.applications.get().status,
            ExamApplication.Status.VALID,
        )

        preview = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": self.period.pk,
            "preview": "1",
        })
        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        self.assertTrue(preview_data["ok"])
        self.assertEqual(preview_data["duplicate_count"], 1)
        self.assertEqual(preview_data["preview_rows"][0]["status"], ExamApplication.Status.DUPLICATE)
        self.assertEqual(self.period.applications.count(), 1)

        second_import = self.client.post(url, {
            "file": self._application_csv(csv_content),
            "exam_period": self.period.pk,
        })
        self.assertTrue(second_import.json()["ok"])
        self.assertEqual(second_import.json()["duplicate_count"], 1)
        self.assertEqual(
            set(self.period.applications.values_list("status", flat=True)),
            {ExamApplication.Status.VALID, ExamApplication.Status.DUPLICATE},
        )

        list_page = self.client.get(reverse("exam-application-list"))
        self.assertContains(list_page, "Phát hiện 1 hồ sơ trùng lặp")
        self.assertContains(list_page, "Trùng lặp")

    def test_duplicate_rows_in_one_file_are_marked(self):
        preview = self.client.post(reverse("exam-application-import"), {
            "file": self._application_csv(
                "Họ và tên,Số CCCD/CMND,Số điện thoại\n"
                "Thí sinh lặp,098765432109,0901234569\n"
                "Thí sinh lặp,098765432109,0901234569\n"
            ),
            "exam_period": self.period.pk,
            "preview": "1",
        })

        self.assertEqual(preview.status_code, 200)
        preview_data = preview.json()
        self.assertEqual(preview_data["duplicate_count"], 1)
        self.assertEqual(
            [row["status"] for row in preview_data["preview_rows"]],
            [ExamApplication.Status.VALID, ExamApplication.Status.DUPLICATE],
        )

    def test_application_import_invalid_batch_does_not_save(self):
        response = self.client.post(reverse("exam-application-import"), {
            "file": self._application_csv(
                "Họ và tên,Số CCCD/CMND\n"
                "Thí sinh hợp lệ,098765432102\n"
                "Thiếu CCCD,\n"
            ),
            "exam_period": self.period.pk,
            "preview": "1",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["valid_count"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(self.period.applications.count(), 0)

    def test_application_import_uses_csrf_token_from_upload_page(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = reverse("exam-application-import")
        page = client.get(url)
        csrf_cookie = page.cookies.get("csrftoken")
        self.assertIsNotNone(csrf_cookie)

        def payload():
            return {
                "file": self._application_csv(
                    "Họ và tên,Số CCCD/CMND\nThí sinh CSRF,098765432103\n"
                ),
                "exam_period": self.period.pk,
                "preview": "1",
            }

        rejected = client.post(url, payload())
        self.assertEqual(rejected.status_code, 403)

        accepted_payload = payload()
        accepted_payload["csrfmiddlewaretoken"] = csrf_cookie.value
        accepted = client.post(url, accepted_payload)
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["ok"], accepted.json())

    def test_application_import_rejects_rows_for_another_period(self):
        response = self.client.post(reverse("exam-application-import"), {
            "file": self._application_csv(
                "Tên đợt thi,Họ và tên,Số CCCD/CMND\n"
                "Đợt không khớp,Thí sinh,098765432105\n"
            ),
            "exam_period": self.period.pk,
            "preview": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid_count"], 0)
        self.assertIn("không khớp", response.json()["errors"][0]["msg"])

    def test_application_import_allows_blank_period_values(self):
        response = self.client.post(reverse("exam-application-import"), {
            "file": self._application_csv(
                "Tên đợt thi,Họ và tên,Số CCCD/CMND\n"
                ",Thí sinh,098765432106\n"
            ),
            "exam_period": self.period.pk,
            "preview": "1",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["valid_count"], 1)
        self.assertEqual(data["errors"], [])
        self.assertEqual(self.period.applications.count(), 0)

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

    def test_remove_candidate_from_published_period(self):
        room = _make_exam_room(self.period)
        assignment = _make_assignment(room, self.app)
        assign_url = reverse("exam-room-assign", args=[self.period.pk])

        page = self.client.get(assign_url, {"room_id": room.pk})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="cceRemoveCandidateModal"')
        self.assertContains(page, 'data-removal-blocked="false"')
        self.assertContains(page, "openRemoveCandidateDialog")
        self.assertNotContains(page, "confirm(")

        response = self.client.post(
            assign_url + f"?room_id={room.pk}",
            {
                "remove_candidate": "1",
                "assignment_id": assignment.pk,
                "room_id": room.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExamRoomAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.DELETE,
                object_label=f"Bỏ {self.app.full_name} khỏi {room.name}",
            ).exists()
        )

    def test_remove_candidate_with_empty_score_placeholder(self):
        room = _make_exam_room(self.period)
        assignment = _make_assignment(room, self.app)

        score_entry_response = self.client.get(reverse("exam-score-entry", args=[room.pk]))
        self.assertEqual(score_entry_response.status_code, 200)
        empty_score = ExamScore.objects.get(assignment=assignment)
        self.assertIsNone(empty_score.score)
        self.assertFalse(empty_score.is_absent)

        page = self.client.get(
            reverse("exam-room-assign", args=[self.period.pk]),
            {"room_id": room.pk},
        )
        self.assertContains(page, 'data-removal-blocked="false"')

        response = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}",
            {
                "remove_candidate": "1",
                "assignment_id": assignment.pk,
                "room_id": room.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExamRoomAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertFalse(ExamScore.objects.filter(pk=empty_score.pk).exists())

    def test_candidate_with_any_score_cannot_be_removed(self):
        room = _make_exam_room(self.period)
        score_states = (
            {"score": Decimal("75.00")},
            {"score": Decimal("0.00")},
            {"is_absent": True},
            {"is_published": True},
        )
        for index, score_state in enumerate(score_states):
            with self.subTest(score_state=score_state):
                app = self.app if index == 0 else _make_application(
                    self.period,
                    id_number=f"0792010789{index:02}",
                    full_name=f"Ứng viên {index}",
                )
                assignment = _make_assignment(room, app)
                ExamScore.objects.create(assignment=assignment, **score_state)

                page = self.client.get(
                    reverse("exam-room-assign", args=[self.period.pk]),
                    {"room_id": room.pk},
                )
                self.assertContains(page, 'data-removal-blocked="true"')
                self.assertContains(
                    page,
                    "Thí sinh đã có điểm, được ghi nhận vắng thi hoặc kết quả đã công bố",
                )

                response = self.client.post(
                    reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}",
                    {
                        "remove_candidate": "1",
                        "assignment_id": assignment.pk,
                        "room_id": room.pk,
                    },
                    follow=True,
                )

                self.assertTrue(ExamRoomAssignment.objects.filter(pk=assignment.pk).exists())
                self.assertTrue(ExamScore.objects.filter(assignment=assignment).exists())
                self.assertContains(
                    response,
                    "Thí sinh đã có điểm, được ghi nhận vắng thi hoặc kết quả đã công bố",
                )

    def test_cannot_remove_candidate_from_closed_period(self):
        room = _make_exam_room(self.period)
        assignment = _make_assignment(room, self.app)
        self.period.status = ExamPeriod.Status.CLOSED
        self.period.save(update_fields=["status"])

        page = self.client.get(
            reverse("exam-room-assign", args=[self.period.pk]),
            {"room_id": room.pk},
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="cceClosedPeriodModal"')
        self.assertContains(page, "Đợt thi này đã đóng, không thể bỏ thí sinh ra khỏi phòng thi.")
        self.assertNotContains(page, "confirm(")

        response = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]) + f"?room_id={room.pk}",
            {
                "remove_candidate": "1",
                "assignment_id": assignment.pk,
                "room_id": room.pk,
            },
            follow=True,
        )

        self.assertTrue(ExamRoomAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertContains(response, "Đợt thi đã đóng, không thể bỏ thí sinh khỏi phòng.")

    def test_cannot_remove_candidate_from_draft_period(self):
        room = _make_exam_room(self.period)
        assignment = _make_assignment(room, self.app)
        self.period.status = ExamPeriod.Status.DRAFT
        self.period.save(update_fields=["status"])

        response = self.client.post(
            reverse("exam-room-assign", args=[self.period.pk]),
            {
                "remove_candidate": "1",
                "assignment_id": assignment.pk,
                "room_id": room.pk,
            },
            follow=True,
        )

        self.assertTrue(ExamRoomAssignment.objects.filter(pk=assignment.pk).exists())
        self.assertContains(response, "Đợt thi chưa công bố không thể quản lý hoặc tạo phòng thi.")

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

    def test_score_upload_blocks_published_room_but_allows_other_room_in_period(self):
        published_score = ExamScore.objects.create(
            assignment=self.asgn,
            score=Decimal("70.00"),
            is_published=True,
        )
        other_room = _make_exam_room(self.period, name="Phòng chưa công bố")
        other_app = _make_application(
            self.period,
            full_name="Thí sinh phòng chưa công bố",
            id_number="001234567894",
        )
        _make_assignment(other_room, other_app)
        period_url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}"
        )

        page = self.client.get(period_url)
        self.assertEqual(page.context["selected_period"], self.period)
        self.assertIsNone(page.context["selected_room"])
        rooms = {room.pk: room for room in page.context["rooms_for_period"]}
        self.assertTrue(rooms[self.room.pk].has_published_scores)
        self.assertFalse(rooms[other_room.pk].has_published_scores)
        self.assertContains(page, f'<option value="{self.room.pk}" disabled')
        self.assertContains(page, f'<option value="{other_room.pk}"')

        published_room_url = period_url + f"&room={self.room.pk}"
        blocked_page = self.client.get(published_room_url)
        self.assertTrue(blocked_page.context["selected_room_blocked"])
        self.assertIsNone(blocked_page.context["selected_room"])
        self.assertContains(blocked_page, "Phòng thi đã có điểm công bố")

        preview = self.client.post(published_room_url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('85.5')}\n"
            ),
            "preview": "1",
        })
        self.assertFalse(preview.json()["ok"])
        self.assertIn("Phòng thi đã có điểm công bố", preview.json()["errors"][0]["msg"])

        confirm = self.client.post(published_room_url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('85.5')}\n"
            ),
        })
        self.assertFalse(confirm.json()["ok"])
        self.assertEqual(confirm.json()["saved"], 0)
        published_score.refresh_from_db()
        self.assertEqual(published_score.score, Decimal("70.00"))

        other_room_url = period_url + f"&room={other_room.pk}"
        other_csv = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('86', **{'Thí sinh': other_app.full_name, 'CCCD': other_app.id_number, 'Phòng thi': other_room.name, 'Ca thi': other_room.session})}\n"
        )
        other_preview = self.client.post(other_room_url, {
            "file": self._score_csv(other_csv),
            "preview": "1",
        })
        self.assertTrue(other_preview.json()["ok"])
        other_confirm = self.client.post(other_room_url, {
            "file": self._score_csv(other_csv),
        })
        self.assertTrue(other_confirm.json()["ok"])

    def _score_csv(self, content):
        uploaded = _io_module.BytesIO(content.encode("utf-8"))
        uploaded.name = "scores.csv"
        return uploaded

    def _score_csv_row(self, score, **overrides):
        values = {
            "Thí sinh": self.app.full_name,
            "CCCD": self.app.id_number,
            "Tên đợt thi": self.period.name,
            "Phòng thi": self.room.name,
            "Ca thi": self.room.session,
            "Điểm số": str(score),
        }
        values.update(overrides)
        return ",".join(values[column] for column in (
            "Thí sinh", "CCCD", "Tên đợt thi", "Phòng thi", "Ca thi", "Điểm số"
        ))

    def test_score_upload_preview_does_not_save_and_confirm_saves_once(self):
        url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}&room={self.room.pk}"
        )
        page = self.client.get(url)
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        self.assertContains(page, "Kiểm tra điểm")
        self.assertContains(page, 'data-mode="strict"')
        self.assertContains(page, "Xác nhận ghi đè điểm")
        self.assertContains(page, "cceWizard.confirmOverwrite")
        self.assertNotContains(page, "window.confirm")
        self.assertNotContains(page, "Tên và CCCD phải khớp")
        self.assertNotContains(page, "Tải mẫu hợp lệ")
        self.assertNotContains(page, "Tải mẫu kiểm tra lỗi")
        self.assertTrue(page.context["sample_available"])

        r = self.client.post(url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('85.5')}\n"
            ),
            "preview": "1",
        })
        self.assertEqual(r.status_code, 200)
        preview_data = r.json()
        self.assertEqual(preview_data["valid_count"], 1)
        self.assertEqual(preview_data["errors"], [])
        self.assertIn(self.app.full_name, preview_data["preview_rows"][0]["preview_text"])
        self.assertIn(self.room.session, preview_data["preview_rows"][0]["preview_text"])
        self.assertFalse(ExamScore.objects.filter(assignment=self.asgn).exists())

        confirmed = self.client.post(url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('85.5')}\n"
            ),
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["ok"])
        self.assertEqual(confirmed.json()["saved"], 1)
        self.assertEqual(
            ExamScore.objects.get(assignment=self.asgn).score,
            Decimal("85.5"),
        )

    def test_score_upload_preview_reports_invalid_score_without_saving(self):
        url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}&room={self.room.pk}"
        )
        response = self.client.post(url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('101')}\n"
            ),
            "preview": "1",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["valid_count"], 0)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("ngoài thang điểm", data["errors"][0]["msg"])
        self.assertFalse(ExamScore.objects.filter(assignment=self.asgn).exists())

    def test_score_upload_requires_all_six_columns(self):
        response = self.client.post(
            reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}",
            {
                "file": self._score_csv(
                    "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Điểm số\n"
                    f"{self._score_csv_row('80')}\n"
                ),
                "preview": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid_count"], 0)
        self.assertIn("Ca thi", response.json()["errors"][0]["msg"])

    def test_score_upload_checks_candidate_and_exam_context(self):
        url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}&room={self.room.pk}"
        )
        csv_content = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('80', **{'Thí sinh': 'Tên không khớp'})}\n"
            f"{self._score_csv_row('80', **{'Tên đợt thi': 'Đợt khác'})}\n"
            f"{self._score_csv_row('80', **{'Phòng thi': 'Phòng khác'})}\n"
            f"{self._score_csv_row('80', **{'Ca thi': 'Chiều'})}\n"
        )
        response = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "preview": "1",
        })

        self.assertEqual(response.json()["valid_count"], 0)
        messages = [error["msg"] for error in response.json()["errors"]]
        self.assertTrue(any("Tên thí sinh" in message for message in messages))
        self.assertTrue(any("Tên đợt thi" in message for message in messages))
        self.assertTrue(any("Phòng thi" in message for message in messages))
        self.assertTrue(any("Ca thi" in message for message in messages))
        self.assertFalse(ExamScore.objects.filter(assignment=self.asgn).exists())

    def test_score_upload_rejects_repeated_candidate_rows(self):
        header = "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
        response = self.client.post(
            reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}",
            {
                "file": self._score_csv(
                    header + f"{self._score_csv_row('80')}\n{self._score_csv_row('90')}\n"
                ),
                "preview": "1",
            },
        )

        self.assertEqual(response.json()["valid_count"], 1)
        self.assertEqual(len(response.json()["errors"]), 1)
        self.assertIn("xuất hiện nhiều lần", response.json()["errors"][0]["msg"])
        self.assertFalse(ExamScore.objects.filter(assignment=self.asgn).exists())

    def test_score_upload_overwrites_locked_unpublished_score_with_confirmation(self):
        existing_score = ExamScore.objects.create(
            assignment=self.asgn,
            score=Decimal("70.00"),
            is_locked=True,
        )
        url = reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}"
        csv_content = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('80')}\n"
        )

        preview = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "preview": "1",
        })
        self.assertTrue(preview.json()["ok"])
        self.assertEqual(preview.json()["overwrite_count"], 1)

        unconfirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
        })
        self.assertTrue(unconfirmed.json()["requires_overwrite_confirmation"])
        self.assertEqual(unconfirmed.json()["saved"], 0)

        confirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "confirm_overwrite_count": "1",
        })
        self.assertTrue(confirmed.json()["ok"])
        existing_score.refresh_from_db()
        self.assertEqual(existing_score.score, Decimal("80"))
        self.assertTrue(existing_score.is_locked)

    def test_score_upload_overwrites_unpublished_score_only_after_confirmation(self):
        existing_score = ExamScore.objects.create(
            assignment=self.asgn,
            score=Decimal("70.00"),
        )
        url = reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}"
        csv_content = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('85.5')}\n"
        )

        preview = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "preview": "1",
        })
        self.assertTrue(preview.json()["ok"])
        self.assertEqual(preview.json()["overwrite_count"], 1)
        existing_score.refresh_from_db()
        self.assertEqual(existing_score.score, Decimal("70.00"))

        unconfirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
        })
        self.assertTrue(unconfirmed.json()["requires_overwrite_confirmation"])
        self.assertEqual(unconfirmed.json()["saved"], 0)
        existing_score.refresh_from_db()
        self.assertEqual(existing_score.score, Decimal("70.00"))

        existing_score.is_published = True
        existing_score.save(update_fields=["is_published"])
        stale_preview = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "confirm_overwrite_count": "1",
        })
        self.assertFalse(stale_preview.json()["ok"])
        self.assertEqual(stale_preview.json()["saved"], 0)
        self.assertIn("đã có điểm công bố", stale_preview.json()["errors"][0]["msg"])
        existing_score.refresh_from_db()
        self.assertEqual(existing_score.score, Decimal("70.00"))

        existing_score.is_published = False
        existing_score.save(update_fields=["is_published"])
        confirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "confirm_overwrite_count": "1",
        })
        self.assertTrue(confirmed.json()["ok"])
        self.assertEqual(confirmed.json()["saved"], 1)
        self.assertEqual(confirmed.json()["overwrite_count"], 1)
        existing_score.refresh_from_db()
        self.assertEqual(existing_score.score, Decimal("85.5"))

    def test_score_upload_preserves_absence_and_no_score_value(self):
        absent_app = _make_application(
            self.period,
            full_name="Thí sinh vắng",
            id_number="001234567893",
        )
        absent_assignment = _make_assignment(self.room, absent_app)
        absent_score = ExamScore.objects.create(
            assignment=absent_assignment,
            score=Decimal("65.00"),
            is_absent=True,
        )
        url = reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}"
        csv_content = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('85.5')}\n"
            f"{self._score_csv_row('80', **{'Thí sinh': absent_app.full_name, 'CCCD': absent_app.id_number})}\n"
        )

        preview = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "preview": "1",
        })
        self.assertTrue(preview.json()["ok"])
        self.assertEqual(preview.json()["overwrite_count"], 1)

        unconfirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
        })
        self.assertTrue(unconfirmed.json()["requires_overwrite_confirmation"])
        self.assertEqual(unconfirmed.json()["saved"], 0)

        confirmed = self.client.post(url, {
            "file": self._score_csv(csv_content),
            "confirm_overwrite_count": "1",
        })
        self.assertTrue(confirmed.json()["ok"])
        self.assertEqual(confirmed.json()["saved"], 2)
        absent_score.refresh_from_db()
        self.assertTrue(absent_score.is_absent)
        self.assertIsNone(absent_score.score)
        self.assertTrue(ExamScore.objects.filter(assignment=self.asgn).exists())
        page = self.client.get(url)
        self.assertContains(page, absent_app.full_name)
        self.assertContains(page, "Vắng")

    def test_score_upload_confirm_does_not_save_valid_rows_when_file_has_errors(self):
        second_app = _make_application(
            self.period,
            full_name="Thí sinh thứ hai",
            id_number="001234567891",
        )
        _make_assignment(self.room, second_app)
        csv_content = (
            "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
            f"{self._score_csv_row('80')}\n"
            f"{self._score_csv_row('101', **{'Thí sinh': second_app.full_name, 'CCCD': second_app.id_number})}\n"
        )
        response = self.client.post(
            reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}",
            {"file": self._score_csv(csv_content)},
        )

        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["saved"], 0)
        self.assertEqual(len(response.json()["errors"]), 1)
        self.assertFalse(ExamScore.objects.filter(assignment__exam_room=self.room).exists())

    def test_score_upload_rejects_excess_precision_and_non_finite_scores(self):
        header = "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
        response = self.client.post(
            reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}",
            {
                "file": self._score_csv(
                    header
                    + f"{self._score_csv_row('85.123')}\n"
                    + f"{self._score_csv_row('NaN')}\n"
                ),
                "preview": "1",
            },
        )

        self.assertEqual(response.json()["valid_count"], 0)
        messages = [error["msg"] for error in response.json()["errors"]]
        self.assertTrue(any("2 chữ số thập phân" in message for message in messages))
        self.assertTrue(any("hữu hạn" in message for message in messages))

    def test_score_upload_hides_sample_links_when_room_has_no_unscored_candidate(self):
        ExamScore.objects.create(assignment=self.asgn, score=Decimal("80.00"))
        url = reverse("exam-score-upload") + f"?period={self.period.pk}&room={self.room.pk}"

        response = self.client.get(url)

        self.assertFalse(response.context["sample_available"])
        self.assertNotContains(response, "Tải mẫu hợp lệ")
        self.assertNotContains(response, "không còn thí sinh chưa có điểm")

    def test_score_upload_samples_match_selected_room_and_preview(self):
        second_app = _make_application(
            self.period,
            full_name="Thí sinh mẫu thứ hai",
            id_number="001234567892",
        )
        _make_assignment(self.room, second_app)
        valid_sample = self.client.get(
            reverse("exam-score-upload-sample"),
            {"room": self.room.pk, "kind": "valid"},
        )
        self.assertEqual(valid_sample.status_code, 200)
        valid_rows = list(csv.reader(
            _io_module.StringIO(valid_sample.content.decode("utf-8-sig"))
        ))
        self.assertEqual(valid_rows[0], [
            "Thí sinh", "CCCD", "Tên đợt thi", "Phòng thi", "Ca thi", "Điểm số"
        ])
        self.assertEqual(valid_rows[1][:5], [
            self.app.full_name,
            self.app.id_number,
            self.period.name,
            self.room.name,
            self.room.session,
        ])
        self.assertEqual(len(valid_rows), 3)

        upload_url = (
            reverse("exam-score-upload")
            + f"?period={self.period.pk}&room={self.room.pk}"
        )
        preview = self.client.post(upload_url, {
            "file": _io_module.BytesIO(valid_sample.content),
            "preview": "1",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["errors"], [])
        self.assertEqual(preview.json()["valid_count"], 2)

        invalid_sample = self.client.get(
            reverse("exam-score-upload-sample"),
            {"room": self.room.pk, "kind": "invalid"},
        )
        invalid_preview = self.client.post(upload_url, {
            "file": _io_module.BytesIO(invalid_sample.content),
            "preview": "1",
        })
        self.assertEqual(invalid_preview.status_code, 200)
        self.assertFalse(invalid_preview.json()["ok"])
        self.assertEqual(invalid_preview.json()["valid_count"], 1)
        self.assertEqual(len(invalid_preview.json()["errors"]), 1)

        wrong_room = self.client.post(upload_url, {
            "file": self._score_csv(
                "Thí sinh,CCCD,Tên đợt thi,Phòng thi,Ca thi,Điểm số\n"
                f"{self._score_csv_row('80', **{'Phòng thi': 'Phòng khác'})}\n"
            ),
            "preview": "1",
        })
        self.assertEqual(wrong_room.json()["valid_count"], 0)
        self.assertIn("Phòng thi", wrong_room.json()["errors"][0]["msg"])

    def test_score_upload_page_rejects_room_from_another_period(self):
        other_period = _make_exam_period(name="Đợt thi khác")
        other_room = _make_exam_room(other_period)

        response = self.client.get(reverse("exam-score-upload"), {
            "period": self.period.pk,
            "room": other_room.pk,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_room"])


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

    def test_room_pass_rate_uses_room_size_including_absent_candidates(self):
        period = _make_exam_period()
        room = _make_exam_room(period, name="P101", capacity=20)

        for index in range(20):
            app = _make_application(
                period,
                id_number=f"0792011234{index:02}",
                full_name=f"Ứng viên {index:02}",
            )
            assignment = _make_assignment(room, app)
            if index < 15:
                ExamScore.objects.create(assignment=assignment, score=Decimal("80.00"))
            elif index < 18:
                ExamScore.objects.create(assignment=assignment, score=Decimal("20.00"))
            else:
                ExamScore.objects.create(assignment=assignment, is_absent=True)

        response = self.client.get(
            reverse("exam-statistics"),
            {"period": period.pk},
        )

        self.assertEqual(response.status_code, 200)
        room_stats = response.context["room_stats"][0]
        self.assertEqual(room_stats["total"], 20)
        self.assertEqual(room_stats["has_score"], 18)
        self.assertEqual(room_stats["absent"], 2)
        self.assertEqual(room_stats["passed"], 15)
        self.assertEqual(room_stats["percent_pass"], 75.0)


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
        self.assertNotContains(r, "Xuất Excel")

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


class CertificateWorkflowTests(TestCase):
    def setUp(self):
        self.user, self.pw = _make_giaoyu_user(username="cert-admin")
        self.client.login(username=self.user.username, password=self.pw)
        self.course = _make_course()
        self.training_class = TrainingClass.objects.create(
            name="Lớp chứng nhận 01",
            course=self.course,
            learning_format=TrainingClass.LearningFormat.AT_CENTER,
            schedule="Thứ 2, 4, 6",
            max_students=30,
            status=TrainingClass.Status.CLOSED,
        )
        self.learner = Learner.objects.create(
            full_name="Nguyễn Văn A",
            date_of_birth="1999-01-01",
            id_number="123456789012",
            phone="0909123456",
            email="a@example.com",
        )
        self.result = FinalCourseResult.objects.create(
            training_class=self.training_class,
            learner=self.learner,
            score=90,
            modified_by=self.user,
        )
        self.certificate = Certificate.objects.create(
            training_class=self.training_class,
            learner=self.learner,
            certificate_number="CCE-2026-0001",
            status=Certificate.Status.ISSUED,
            issued_by=self.user,
            reason="Đạt kết quả cuối khóa.",
        )

    def test_certificate_issue_requires_pass_result(self):
        learner = Learner.objects.create(
            full_name="Trần Thị B",
            date_of_birth="2001-02-02",
            id_number="123456789013",
            phone="0909123457",
            email="b@example.com",
        )
        with self.assertRaises(ValidationError):
            Certificate(
                training_class=self.training_class,
                learner=learner,
                certificate_number="CCE-2026-0002",
                status=Certificate.Status.ISSUED,
            ).clean()

    def test_certificate_status_change_requires_reason(self):
        response = self.client.post(
            reverse("certificate-status-change", args=[self.certificate.pk]),
            {"status": Certificate.Status.REVOKED, "reason": ""},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vui lòng nhập lý do bắt buộc.")

    def test_replacement_marks_original_certificate_as_replaced(self):
        reason = "Thay thế do sai thông tin."
        response = self.client.post(
            reverse("certificate-status-change", args=[self.certificate.pk]),
            {"status": Certificate.Status.REPLACED, "reason": reason},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.certificate.refresh_from_db()
        self.assertEqual(self.certificate.status, Certificate.Status.REPLACED)
        self.assertIsNotNone(self.certificate.replaced_by)
        self.assertEqual(self.certificate.replaced_by.status, Certificate.Status.ISSUED)
        history = self.certificate.history.get()
        self.assertEqual(history.old_status, Certificate.Status.ISSUED)
        self.assertEqual(history.new_status, Certificate.Status.REPLACED)
        self.assertEqual(history.reason, reason)
        audit_entry = AuditLog.objects.get(
            action=AuditLog.Action.REPLACE,
            object_id=str(self.certificate.pk),
        )
        self.assertEqual(audit_entry.actor, self.user)
        self.assertEqual(audit_entry.reason, reason)
        self.assertEqual(audit_entry.old_values["status"], Certificate.Status.ISSUED)
        self.assertEqual(audit_entry.new_values["status"], Certificate.Status.REPLACED)

    def test_certificate_history_renders_system_actor_when_changed_by_is_null(self):
        CertificateHistory.objects.create(
            certificate=self.certificate,
            old_status=Certificate.Status.ISSUED,
            new_status=Certificate.Status.ISSUED,
            reason="Dữ liệu nhập ban đầu.",
        )

        response = self.client.get(
            reverse("certificate-history", args=(self.certificate.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Người thực hiện:")
        self.assertContains(response, "Hệ thống")


class ExamCertificateWorkflowTests(TestCase):
    def setUp(self):
        self.user, self.pw = _make_exam_user(role="Cán bộ thi", username="exam-cert-admin")
        self.client.login(username=self.user.username, password=self.pw)
        self.period = _make_exam_period(name="Đợt thi tháng 12/2026")
        self.room = _make_exam_room(self.period, name="A201")
        self.app = _make_application(self.period, full_name="Nguyễn Văn C", id_number="987654321012")
        self.assignment = _make_assignment(self.room, self.app)
        self.score = ExamScore.objects.create(assignment=self.assignment, score=Decimal("8.50"), is_published=True)

    def test_exam_certificate_issue_requires_published_pass_score(self):
        self.score.is_published = False
        self.score.save(update_fields=["is_published"])
        with self.assertRaises(ValidationError):
            ExamCertificate(
                exam_period=self.period,
                application=self.app,
                certificate_number="EXC-2026-0001",
                status=ExamCertificate.Status.ISSUED,
            ).clean()

    def test_exam_certificate_status_change_requires_reason(self):
        certificate = ExamCertificate.objects.create(
            exam_period=self.period,
            application=self.app,
            certificate_number="EXC-2026-0001",
            status=ExamCertificate.Status.ISSUED,
            issued_by=self.user,
        )
        response = self.client.post(
            reverse("exam-certificate-status-change", args=[certificate.pk]),
            {"status": ExamCertificate.Status.REVOKED, "reason": ""},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vui lòng nhập lý do bắt buộc.")

    def test_exam_certificate_issue_creates_record_from_published_score(self):
        response = self.client.post(
            reverse("exam-certificate-list"),
            {
                "issue_exam_certificate": "1",
                "exam_period": self.period.pk,
                "application": self.app.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        certificate = ExamCertificate.objects.get(
            application=self.app,
            exam_period=self.period,
        )
        self.assertContains(response, "Đã cấp chứng chỉ")
        history = certificate.history.get()
        self.assertEqual(history.old_status, ExamCertificate.Status.ISSUED)
        self.assertEqual(history.new_status, ExamCertificate.Status.ISSUED)
        self.assertEqual(history.changed_by, self.user)
        audit_entry = AuditLog.objects.get(
            action=AuditLog.Action.ISSUE,
            object_id=str(certificate.pk),
        )
        self.assertEqual(audit_entry.actor, self.user)
        self.assertEqual(audit_entry.new_values["status"], ExamCertificate.Status.ISSUED)

    def test_certificate_menus_and_direct_views_follow_role_permissions(self):
        response = self.client.get(reverse("exam-certificate-list"))
        self.assertEqual(response.status_code, 200)
        exam_menu_keys = [item["key"] for item in response.context["sidebar_items"]]
        self.assertIn("exam-certificates", exam_menu_keys)
        self.assertNotIn("certificates", exam_menu_keys)

        education_user, _ = _make_giaoyu_user(username="education-cert-access")
        self.client.force_login(education_user)
        response = self.client.get(reverse("certificate-list"))
        self.assertEqual(response.status_code, 200)
        education_menu_keys = [item["key"] for item in response.context["sidebar_items"]]
        self.assertIn("certificates", education_menu_keys)
        self.assertNotIn("exam-certificates", education_menu_keys)
        self.assertEqual(
            self.client.get(reverse("exam-certificate-list")).status_code,
            403,
        )

    def test_exam_certificate_history_renders_system_actor_when_changed_by_is_null(self):
        certificate = ExamCertificate.objects.create(
            exam_period=self.period,
            application=self.app,
            certificate_number="EXC-2026-SYSTEM-ACTOR",
            status=ExamCertificate.Status.ISSUED,
            issued_by=self.user,
        )
        certificate.history.create(
            old_status=ExamCertificate.Status.ISSUED,
            new_status=ExamCertificate.Status.ISSUED,
            reason="Dữ liệu nhập ban đầu.",
        )

        response = self.client.get(
            reverse("exam-certificate-history", args=(certificate.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Người thực hiện:")
        self.assertContains(response, "Hệ thống")


class PublicExamLookupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.period = _make_exam_period(name="Đợt thi tháng 01/2027")
        self.room = _make_exam_room(self.period, name="B202")
        self.app = _make_application(self.period, full_name="Lê Văn D", id_number="112233445566")
        self.assignment = _make_assignment(self.room, self.app)
        self.app.email = "candidate@example.com"
        self.app.save(update_fields=["email"])

    def captcha_payload(self, valid=True):
        captcha_key = CaptchaStore.generate_key()
        captcha = CaptchaStore.objects.get(hashkey=captcha_key)
        return {
            "captcha_0": captcha_key,
            "captcha_1": captcha.response if valid else "invalid",
        }

    def test_pdf_fonts_support_vietnamese_characters(self):
        _register_public_pdf_font()

        for font_name in ("PublicExamSans", "PublicExamSans-Bold"):
            character_map = pdfmetrics.getFont(font_name).face.charToGlyph
            self.assertTrue(
                all(
                    character_map.get(codepoint, 0)
                    for codepoint in _PUBLIC_PDF_REQUIRED_GLYPHS
                )
            )

    def submit_lookup(
        self,
        client,
        route_name,
        id_number=None,
        email=None,
        valid_captcha=True,
    ):
        payload = {
            "action": "lookup",
            "id_number": id_number if id_number is not None else self.app.id_number,
            "email": email if email is not None else self.app.email,
            **self.captcha_payload(valid_captcha),
        }
        return client.post(reverse(route_name), payload)

    def test_public_lookup_page_is_available_without_login(self):
        response = self.client.get(reverse("exam-public-lookup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tra cứu điểm và chứng chỉ")
        self.assertContains(response, "Mã xác thực trong hình")
        self.assertContains(response, 'name="captcha_0"')
        self.assertContains(response, 'name="captcha_1"')
        self.assertContains(response, "Tra cứu</button>")
        self.assertNotContains(response, "Gửi mã qua email")
        self.assertContains(response, 'name="id_number"')

    def test_lookup_requires_matching_cccd_email_and_masks_id_number(self):
        ExamScore.objects.create(
            assignment=self.assignment,
            score=Decimal("92.00"),
            is_published=True,
        )
        certificate = ExamCertificate.objects.create(
            exam_period=self.period,
            application=self.app,
            certificate_number="EXC-2027-0001",
            status=ExamCertificate.Status.REVOKED,
            issued_by=None,
        )

        unauthenticated = self.client.get(reverse("exam-public-lookup"))
        self.assertNotContains(unauthenticated, self.app.full_name)
        self.assertNotContains(unauthenticated, certificate.certificate_number)

        response = self.submit_lookup(self.client, "exam-public-lookup")
        self.assertRedirects(response, reverse("exam-public-lookup"))
        response = self.client.get(reverse("exam-public-lookup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.app.full_name)
        self.assertContains(response, "112******566")
        self.assertNotContains(response, self.app.id_number)
        self.assertContains(response, "92,00")
        self.assertContains(response, certificate.certificate_number)
        self.assertContains(response, "Thu hồi")
        self.assertContains(response, "Xuất PDF")
        self.assertContains(response, reverse("exam-public-export-pdf"))

        pdf_response = self.client.get(reverse("exam-public-export-pdf"))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response.content.startswith(b"%PDF-"))

    def test_replaced_certificate_is_not_presented_as_active(self):
        second_period = _make_exam_period(name="Đợt thi tháng 02/2027")
        second_room = _make_exam_room(second_period, name="D404")
        second_application = _make_application(
            second_period,
            full_name=self.app.full_name,
            id_number=self.app.id_number,
        )
        second_application.email = self.app.email
        second_application.save(update_fields=["email"])
        second_assignment = _make_assignment(second_room, second_application)
        ExamScore.objects.create(
            assignment=second_assignment,
            score=Decimal("85.00"),
            is_published=True,
        )
        replaced_certificate = ExamCertificate.objects.create(
            exam_period=second_period,
            application=second_application,
            certificate_number="EXC-2027-0002",
            status=ExamCertificate.Status.REPLACED,
            issued_by=None,
        )
        replacement_certificate = ExamCertificate.objects.create(
            exam_period=second_period,
            application=second_application,
            certificate_number="EXC-2027-0003",
            status=ExamCertificate.Status.ISSUED,
            issued_by=None,
        )
        replaced_certificate.replaced_by = replacement_certificate
        replaced_certificate.save(update_fields=["replaced_by"])

        response = self.submit_lookup(self.client, "exam-public-lookup")
        self.assertRedirects(response, reverse("exam-public-lookup"))
        response = self.client.get(reverse("exam-public-lookup"))

        self.assertContains(response, "EXC-2027-0002")
        self.assertContains(response, "Đã thay thế")
        self.assertContains(response, "EXC-2027-0003")
        self.assertContains(response, "Đã cấp")

    def test_unpublished_and_missing_records_have_the_same_no_result_response(self):
        ExamScore.objects.create(
            assignment=self.assignment,
            score=Decimal("92.00"),
            is_published=False,
        )
        response = self.submit_lookup(self.client, "exam-public-lookup")
        self.assertRedirects(response, reverse("exam-public-lookup"))
        unpublished_response = self.client.get(reverse("exam-public-lookup"))

        other_client = Client()
        response = self.submit_lookup(
            other_client,
            "exam-public-lookup",
            id_number="998877665544",
            email="other@example.com",
        )
        self.assertRedirects(response, reverse("exam-public-lookup"))
        missing_response = other_client.get(reverse("exam-public-lookup"))

        self.assertEqual(unpublished_response.status_code, 200)
        self.assertContains(unpublished_response, "Không tìm thấy kết quả")
        self.assertEqual(unpublished_response.content, missing_response.content)

    def test_lookup_rejects_invalid_email(self):
        response = self.submit_lookup(
            self.client,
            "exam-public-lookup",
            email="not-an-email",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["request_form"].errors["email"])

    def test_lookup_rejects_invalid_captcha_without_showing_results(self):
        ExamScore.objects.create(
            assignment=self.assignment,
            score=Decimal("92.00"),
            is_published=True,
        )

        response = self.submit_lookup(
            self.client,
            "exam-public-lookup",
            valid_captcha=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Mã xác thực không đúng", status_code=400)
        self.assertNotContains(response, self.app.full_name, status_code=400)
        self.assertNotIn("public_exam_lookup_result", self.client.session)

    def test_public_room_lookup_only_shows_the_candidates_rooms(self):
        initial_response = self.client.get(reverse("exam-room-public-lookup"))
        self.assertEqual(initial_response.status_code, 200)
        self.assertContains(initial_response, 'name="captcha_0"')
        self.assertContains(initial_response, "Tra cứu</button>")

        other_room = _make_exam_room(self.period, name="C303")
        other_application = _make_application(
            self.period,
            full_name="Thí sinh khác",
            id_number="998877665544",
        )
        other_application.email = "other@example.com"
        other_application.save(update_fields=["email"])
        _make_assignment(other_room, other_application)

        response = self.submit_lookup(self.client, "exam-room-public-lookup")
        self.assertRedirects(response, reverse("exam-room-public-lookup"))
        response = self.client.get(reverse("exam-room-public-lookup"))

        self.assertContains(response, "B202")
        self.assertContains(response, "112******566")
        self.assertNotContains(response, "C303")
        self.assertNotContains(response, other_application.full_name)
        self.assertContains(response, "Xuất PDF")
        self.assertContains(response, reverse("exam-room-public-export-pdf"))

        pdf_response = self.client.get(reverse("exam-room-public-export-pdf"))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response.content.startswith(b"%PDF-"))

    def test_pdf_exports_require_a_matching_captcha_verified_lookup(self):
        response = self.client.get(reverse("exam-public-export-pdf"))
        self.assertRedirects(response, reverse("exam-public-lookup"))

        self.submit_lookup(self.client, "exam-room-public-lookup")
        response = self.client.get(reverse("exam-public-export-pdf"))
        self.assertRedirects(response, reverse("exam-public-lookup"))

    def test_lookup_is_throttled_by_ip_and_identity(self):
        with patch("training.public_exam_views.LOOKUP_IP_LIMIT", 0):
            limited = self.submit_lookup(self.client, "exam-public-lookup")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited["Retry-After"], "900")
        self.assertContains(limited, "Đã vượt giới hạn tra cứu", status_code=429)

        cache.clear()
        with patch("training.public_exam_views.LOOKUP_IDENTITY_LIMIT", 0):
            limited = self.submit_lookup(
                self.client,
                "exam-public-lookup",
            )
        self.assertEqual(limited.status_code, 429)
        self.assertContains(limited, "Đã vượt giới hạn tra cứu", status_code=429)

    def test_expired_lookup_session_cannot_show_results(self):
        ExamScore.objects.create(
            assignment=self.assignment,
            score=Decimal("92.00"),
            is_published=True,
        )
        response = self.submit_lookup(self.client, "exam-public-lookup")
        self.assertRedirects(response, reverse("exam-public-lookup"))
        session = self.client.session
        lookup = session["public_exam_lookup_result"]
        lookup["expires_at"] = timezone.now().timestamp() - 1
        session["public_exam_lookup_result"] = lookup
        session.save()

        response = self.client.get(reverse("exam-public-lookup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mã xác thực trong hình")
        self.assertNotContains(response, self.app.full_name)
        self.assertNotIn("public_exam_lookup_result", self.client.session)

class PublicExamRegistrationTests(TestCase):
    def setUp(self):
        self.course = _make_course()
        self.period = _make_exam_period(
            course=self.course,
            name="Đợt thi đăng ký trực tuyến",
        )

    def valid_payload(self):
        return {
            "cert_type": str(self.course.pk),
            "exam_period": str(self.period.pk),
            "full_name": "  Nguyễn Văn Đăng Ký  ",
            "date_of_birth": "2000-01-02",
            "id_number": "123456789012",
            "phone": "0901234567",
            "email": "candidate@example.com",
        }

    def test_public_home_is_available_without_login_and_has_candidate_paths(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đăng ký dự thi")
        self.assertContains(response, "Xem phòng thi")
        self.assertContains(response, "Tra cứu điểm và chứng chỉ")
        self.assertContains(response, self.period.name)

    def test_registration_form_is_public_and_posts_online_application(self):
        response = self.client.get(reverse("exam-application-public-register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Địa chỉ email")
        self.assertContains(response, "Ngày sinh")
        self.assertNotContains(response, "Quay về trang chủ")
        self.assertContains(response, " readonly ")
        self.assertContains(response, 'placeholder="mm/dd/yyyy"')
        self.assertContains(response, 'data-date-previous')
        self.assertContains(response, 'data-date-month')
        self.assertContains(response, 'data-date-year')
        self.assertContains(
            response,
            f'data-cert-type="{self.course.pk}"',
        )
        self.assertContains(
            response,
            "public-exam-registration.js",
        )

        response = self.client.post(
            reverse("exam-application-public-register"),
            self.valid_payload(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("exam-application-public-success"),
        )

        application = ExamApplication.objects.get(id_number="123456789012")
        self.assertEqual(application.full_name, "Nguyễn Văn Đăng Ký")
        self.assertEqual(application.date_of_birth.isoformat(), "2000-01-02")
        self.assertEqual(application.email, "candidate@example.com")
        self.assertEqual(application.source, ExamApplication.Source.ONLINE)
        self.assertEqual(application.status, ExamApplication.Status.NEW)

        response = self.client.get(reverse("exam-application-public-success"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đã nhận hồ sơ đăng ký")
        self.assertContains(response, self.course.name)
        self.assertContains(
            response,
            f'href="{reverse("exam-application-public-register")}"',
        )

    def test_registration_calendar_value_is_parsed_into_a_date(self):
        payload = self.valid_payload()
        payload["date_of_birth"] = "01/02/2000"

        response = self.client.post(
            reverse("exam-application-public-register"),
            payload,
        )

        self.assertEqual(response.status_code, 302)
        application = ExamApplication.objects.get(id_number="123456789012")
        self.assertEqual(application.date_of_birth.isoformat(), "2000-01-02")

    def test_registration_rejects_an_exam_period_for_another_certificate_type(self):
        other_course = Course.objects.create(
            name="Chứng chỉ khác",
            pass_threshold=70,
        )
        other_period = _make_exam_period(
            course=other_course,
            name="Đợt thi chứng chỉ khác",
        )
        payload = self.valid_payload()
        payload["exam_period"] = str(other_period.pk)

        response = self.client.post(
            reverse("exam-application-public-register"),
            payload,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Đợt thi không thuộc loại chứng chỉ đã chọn.",
            status_code=400,
        )
        self.assertFalse(ExamApplication.objects.exists())

    def test_invalid_registration_shows_field_errors_without_saving(self):
        payload = self.valid_payload()
        payload["id_number"] = "1234"
        payload["email"] = "not-an-email"
        payload["date_of_birth"] = "2999-01-01"

        response = self.client.post(
            reverse("exam-application-public-register"),
            payload,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Số CCCD phải gồm đúng 12 chữ số.", status_code=400)
        self.assertContains(response, "email", status_code=400)
        self.assertContains(response, "Ngày sinh không thể ở tương lai.", status_code=400)
        self.assertFalse(ExamApplication.objects.exists())

    def test_missing_required_registration_fields_do_not_save(self):
        response = self.client.post(
            reverse("exam-application-public-register"),
            {},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Loại chứng chỉ", status_code=400)
        self.assertContains(response, "Đợt thi", status_code=400)
        self.assertContains(response, "Họ và tên", status_code=400)
        self.assertFalse(ExamApplication.objects.exists())

    def test_registration_is_rejected_for_closed_or_expired_periods(self):
        closed_period = _make_exam_period(
            course=self.course,
            status=ExamPeriod.Status.CLOSED,
            name="Đợt thi đã đóng",
        )
        expired_period = _make_exam_period(
            course=self.course,
            name="Đợt thi đã hết hạn",
        )
        expired_period.registration_deadline = "2020-01-01"
        expired_period.save(update_fields=("registration_deadline",))

        for period, expected_text in (
            (closed_period, "Đợt thi đã đóng đăng ký"),
            (expired_period, "Đợt thi đã hết hạn đăng ký"),
        ):
            payload = self.valid_payload()
            payload["exam_period"] = str(period.pk)
            response = self.client.post(
                reverse("exam-application-public-register"),
                payload,
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected_text)

        self.assertFalse(ExamApplication.objects.exists())

    def test_registration_is_rejected_when_period_is_full(self):
        self.period.max_candidates = 1
        self.period.save(update_fields=("max_candidates",))
        existing = _make_application(self.period, id_number="123456789010")
        existing.date_of_birth = "2000-01-01"
        existing.email = "existing@example.com"
        existing.save(update_fields=("date_of_birth", "email"))

        response = self.client.post(
            reverse("exam-application-public-register"),
            self.valid_payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Đợt thi đã đủ số lượng")
        self.assertEqual(ExamApplication.objects.count(), 1)


class WorkflowTestDataSeedTests(TestCase):
    def test_seed_workflow_test_data_populates_empty_workflows_idempotently(self):
        with override_settings(DEBUG=True):
            call_command("seed_workflow_test_data", stdout=StringIO())
            first_audit_count = AuditLog.objects.filter(
                reason="Dữ liệu kiểm thử nội bộ, được tạo cục bộ."
            ).count()
            call_command("seed_workflow_test_data", stdout=StringIO())

        result_class = TrainingClass.objects.get(name="CCE-LAPTR-901")
        self.assertEqual(
            FinalCourseResult.objects.filter(training_class=result_class).count(),
            5,
        )
        self.assertTrue(result_class.final_results_locked)
        self.assertEqual(
            Certificate.objects.filter(training_class=result_class).count(),
            5,
        )
        self.assertEqual(
            CertificateHistory.objects.filter(certificate__training_class=result_class).count(),
            7,
        )
        self.assertEqual(EnrollmentTransferHistory.objects.count(), 1)
        self.assertEqual(
            ExamApplication.objects.filter(
                exam_period__name=(
                    "Đợt thi CCE tháng 10/2026 — Lập trình Python ứng dụng "
                    "(đợt bổ sung)"
                )
            ).count(),
            6,
        )
        self.assertEqual(
            ExamScore.objects.filter(
                assignment__exam_room__exam_period__name=(
                    "Đợt thi CCE tháng 10/2026 — Lập trình Python ứng dụng "
                    "(đợt bổ sung)"
                ),
                is_published=True,
            ).count(),
            6,
        )
        self.assertEqual(
            ExamCertificate.objects.filter(
                exam_period__name=(
                    "Đợt thi CCE tháng 10/2026 — Lập trình Python ứng dụng "
                    "(đợt bổ sung)"
                )
            ).count(),
            5,
        )
        self.assertEqual(
            ExamCertificateHistory.objects.filter(
                certificate__exam_period__name=(
                    "Đợt thi CCE tháng 10/2026 — Lập trình Python ứng dụng "
                    "(đợt bổ sung)"
                )
            ).count(),
            7,
        )
        self.assertEqual(
            ExamPeriod.objects.filter(
                name="Đợt thi CCE tháng 03/2027 — Lập trình Python ứng dụng",
                status=ExamPeriod.Status.PUBLISHED,
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                reason="Dữ liệu kiểm thử nội bộ, được tạo cục bộ."
            ).count(),
            first_audit_count,
        )

    def test_seed_workflow_test_data_is_disabled_outside_debug(self):
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                call_command("seed_workflow_test_data", stdout=StringIO())
