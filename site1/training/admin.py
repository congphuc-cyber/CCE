from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.sites import UnfoldAdminSite

from training.access import user_has_feature
from training.audit import record_audit, snapshot_account, snapshot_instance
from training.forms import AccountAdminForm, CourseAdminForm, TeacherAdminForm, TrainingClassAdminForm
from training.models import AuditLog, Course, Learner, Teacher, TrainingClass
from training.navigation import ROLE_ORDER, roles_for_user


def can_manage_classes(user):
    roles = roles_for_user(user)
    return user.is_superuser or (
        "Giáo viên" not in roles and user_has_feature(user, "classes")
    )


class CCEAdminSite(UnfoldAdminSite):
    site_header = "Trung tâm Kỹ thuật Điện toán"
    site_title = "Quản lý Đào tạo và Khảo thí"
    index_title = "Quản trị hệ thống"
    enable_nav_sidebar = True

    def has_permission(self, request):
        user = request.user
        return super().has_permission(request) or (
            user.is_authenticated and user.is_active and bool(roles_for_user(user))
        )

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)
        for app in app_list:
            if app["app_label"] == "auth":
                app["name"] = "Quản trị tài khoản"
            elif app["app_label"] in ("home", "training"):
                app["name"] = "Quản lý đào tạo"
        return app_list


role_admin_site = CCEAdminSite(name="admin")


class BaseModelAdmin(UnfoldModelAdmin):
    allow_delete = False
    change_list_title = "Danh sách"
    list_per_page = 25
    list_max_show_all = 100
    show_full_result_count = False
    show_facets = admin.ShowFacets.NEVER
    empty_value_display = "Chưa có thông tin"
    search_help_text = "Nhập từ khóa để tìm kiếm."
    feature_key = None

    def has_feature_access(self, request):
        return bool(self.feature_key and user_has_feature(request.user, self.feature_key))

    def has_role_or_model_permission(self, request, permission_check):
        if roles_for_user(request.user):
            return self.has_feature_access(request)
        return permission_check()

    def has_delete_permission(self, request, obj=None):
        return self.allow_delete and super().has_delete_permission(request, obj)

    def has_module_permission(self, request):
        return self.has_role_or_model_permission(
            request,
            lambda: super(BaseModelAdmin, self).has_module_permission(request),
        )

    def has_view_permission(self, request, obj=None):
        return self.has_role_or_model_permission(
            request,
            lambda: super(BaseModelAdmin, self).has_view_permission(request, obj),
        )

    def has_add_permission(self, request):
        return self.has_role_or_model_permission(
            request,
            lambda: super(BaseModelAdmin, self).has_add_permission(request),
        )

    def has_change_permission(self, request, obj=None):
        return self.has_role_or_model_permission(
            request,
            lambda: super(BaseModelAdmin, self).has_change_permission(request, obj),
        )

    def changelist_view(self, request, extra_context=None):
        context = {**(extra_context or {}), "title": self.change_list_title}
        return super().changelist_view(request, extra_context=context)

    def save_model(self, request, obj, form, change):
        old_values = None
        if change and form.changed_data:
            old_values = snapshot_instance(self.model._default_manager.get(pk=obj.pk))
        super().save_model(request, obj, form, change)
        obj._audit_old_values = old_values
        obj._audit_action = AuditLog.Action.EDIT if change else AuditLog.Action.CREATE

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        if not change or getattr(obj, "_audit_old_values", None) is not None:
            record_audit(
                request.user,
                obj._audit_action,
                obj,
                old_values=obj._audit_old_values,
                new_values=snapshot_instance(obj),
            )


class CourseAdmin(BaseModelAdmin):
    feature_key = "courses"
    form = CourseAdminForm
    change_list_title = "Danh sách khóa học"
    list_display = ("name", "threshold_display", "class_count_display", "edit_action")
    list_display_links = None
    search_fields = ("name",)
    ordering = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_class_count=Count("classes", distinct=True))

    @admin.display(description="Ngưỡng đạt")
    def threshold_display(self, obj):
        return f"{obj.pass_threshold:.2f}%"

    @admin.display(description="Số lớp")
    def class_count_display(self, obj):
        return obj._class_count

    @admin.display(description="Hành động")
    def edit_action(self, obj):
        try:
            url = reverse(f"{self.admin_site.name}:training_course_change", args=(obj.pk,))
        except Exception:
            url = reverse(f"{self.admin_site.name}:home_course_change", args=(obj.pk,))
        return format_html('<a class="cce-table-btn" href="{}">Sửa</a>', url)


class TrainingClassAdmin(BaseModelAdmin):
    feature_key = "classes"
    form = TrainingClassAdminForm
    change_list_title = "Danh sách lớp học"
    list_display = (
        "name",
        "course",
        "format_display",
        "schedule",
        "capacity_display",
        "status_badge",
        "edit_action",
        "detail_action",
    )
    list_display_links = None
    list_filter = ("course", "learning_format", "status")
    search_fields = ("name", "course__name")
    ordering = ("name",)
    fields = ("name", "course", "learning_format", "schedule", "max_students")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("course")
            .annotate(_enrolled_count=Count("enrollments", distinct=True))
        )

    @admin.display(description="Hình thức")
    def format_display(self, obj):
        return obj.get_learning_format_display()

    @admin.display(description="Sĩ số hiện tại / tối đa")
    def capacity_display(self, obj):
        return f"{obj._enrolled_count} / {obj.max_students}"

    @admin.display(description="Trạng thái")
    def status_badge(self, obj):
        status = obj.get_status_display()
        cls = "cce-badge-open" if obj.status == TrainingClass.Status.OPEN else "cce-badge-closed"
        return format_html('<span class="cce-badge {}">{}</span>', cls, status)

    @admin.display(description="Sửa")
    def edit_action(self, obj):
        try:
            url = reverse(f"{self.admin_site.name}:training_trainingclass_change", args=(obj.pk,))
        except Exception:
            url = reverse(f"{self.admin_site.name}:home_trainingclass_change", args=(obj.pk,))
        return format_html('<a class="cce-table-btn" href="{}">Sửa</a>', url)

    @admin.display(description="Xem")
    def detail_action(self, obj):
        url = reverse("class-detail", args=(obj.pk,))
        return format_html('<a class="cce-table-btn cce-table-btn-outline" href="{}">Xem</a>', url)


class TeacherAdmin(BaseModelAdmin):
    feature_key = "teachers"
    form = TeacherAdminForm
    change_list_title = "Danh sách giáo viên"
    list_display = ("full_name", "contact_details", "classes_summary", "edit_action")
    list_display_links = None
    search_fields = ("full_name", "phone", "email")
    ordering = ("full_name",)

    @admin.display(description="Liên hệ")
    def contact_details(self, obj):
        return " · ".join(value for value in (obj.phone, obj.email) if value) or "Chưa cập nhật"

    @admin.display(description="Các lớp phụ trách")
    def classes_summary(self, obj):
        names = obj.teacher_assignments.filter(is_active=True).values_list(
            "training_class__name",
            flat=True,
        )
        return ", ".join(names) or "Chưa phân công"

    @admin.display(description="Hành động")
    def edit_action(self, obj):
        try:
            url = reverse(f"{self.admin_site.name}:training_teacher_change", args=(obj.pk,))
        except Exception:
            url = reverse(f"{self.admin_site.name}:home_teacher_change", args=(obj.pk,))
        return format_html('<a class="cce-table-btn" href="{}">Sửa</a>', url)


class LearnerAdmin(BaseModelAdmin):
    feature_key = "learners"
    list_display = ("full_name", "phone", "email")
    search_fields = ("full_name", "phone", "email")
    ordering = ("full_name",)


class CceRoleFilter(SimpleListFilter):
    title = "Vai trò"
    parameter_name = "cce_role"

    def lookups(self, request, model_admin):
        return tuple((role, role) for role in ROLE_ORDER)

    def queryset(self, request, queryset):
        if self.value() not in ROLE_ORDER:
            return queryset
        return queryset.filter(groups__name=self.value())


class AccountStatusFilter(SimpleListFilter):
    title = "Trạng thái"
    parameter_name = "is_active"

    def lookups(self, request, model_admin):
        return (("1", "Đang hoạt động"), ("0", "Đã khóa"))

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(is_active=True)
        if self.value() == "0":
            return queryset.filter(is_active=False)
        return queryset


class AccountAdmin(BaseModelAdmin):
    feature_key = "accounts"
    form = AccountAdminForm
    change_list_title = "Danh sách tài khoản"
    list_display = (
        "full_name_display",
        "username",
        "email",
        "role_display",
        "status_display",
        "edit_action",
    )
    list_display_links = None
    list_filter = (CceRoleFilter, AccountStatusFilter)
    search_fields = ("first_name", "last_name", "username", "email")
    ordering = ("username",)
    fieldsets = (
        ("Thông tin tài khoản", {"fields": ("full_name", "username", "email", "role")}),
        ("Bảo mật và trạng thái", {"fields": ("initial_password", "is_active")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")

    @admin.display(description="Họ và tên")
    def full_name_display(self, obj):
        return obj.get_full_name() or "Chưa cập nhật"

    @admin.display(description="Tên đăng nhập")
    def username(self, obj):
        return obj.username

    @admin.display(description="Hành động")
    def edit_action(self, obj):
        url = reverse(f"{self.admin_site.name}:auth_user_change", args=(obj.pk,))
        return format_html('<a class="cce-table-btn" href="{}">Sửa</a>', url)

    @admin.display(description="Vai trò")
    def role_display(self, obj):
        roles = obj.groups.filter(name__in=ROLE_ORDER).values_list("name", flat=True)
        return " · ".join(roles) or "Chưa phân vai trò"

    @admin.display(description="Trạng thái")
    def status_display(self, obj):
        label = "Đang hoạt động" if obj.is_active else "Đã khóa"
        cls = "cce-badge-valid" if obj.is_active else "cce-badge-duplicate"
        return format_html('<span class="cce-badge {}">{}</span>', cls, label)

    def save_model(self, request, obj, form, change):
        old_values = None
        if change and form.changed_data:
            old_values = snapshot_account(get_user_model()._default_manager.get(pk=obj.pk))

        full_name = form.cleaned_data["full_name"].strip().split(maxsplit=1)
        obj.first_name = full_name[0] if full_name else ""
        obj.last_name = full_name[1] if len(full_name) > 1 else ""
        password = form.cleaned_data.get("initial_password")
        if password:
            obj.set_password(password)
        admin.ModelAdmin.save_model(self, request, obj, form, change)
        obj.groups.set((form.cleaned_data["role"],))

        if not change or old_values is not None:
            record_audit(
                request.user,
                AuditLog.Action.EDIT if change else AuditLog.Action.CREATE,
                obj,
                old_values=old_values,
                new_values=snapshot_account(obj),
            )

    def save_related(self, request, form, formsets, change):
        admin.ModelAdmin.save_related(self, request, form, formsets, change)


account_model = get_user_model()
account_model._meta.verbose_name = "tài khoản"
account_model._meta.verbose_name_plural = "Tài khoản"

role_admin_site.register(Course, CourseAdmin)
role_admin_site.register(TrainingClass, TrainingClassAdmin)
role_admin_site.register(Teacher, TeacherAdmin)
role_admin_site.register(Learner, LearnerAdmin)
role_admin_site.register(account_model, AccountAdmin)
