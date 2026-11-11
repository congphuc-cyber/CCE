import os
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import BaseCommand

from training.navigation import ROLE_MENUS


class Command(BaseCommand):
    help = "Khởi tạo hệ thống toàn diện: migrate, tạo 4 nhóm vai trò, tạo superuser và nạp dữ liệu mẫu (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Nạp dữ liệu mẫu giả lập cho quá trình phát triển (khóa học, lớp học, học viên, giáo viên).",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=== KHỞI TẠO HỆ THỐNG CCE ==="))

        # 1. Chạy migrate
        self.stdout.write("1. Áp dụng migrations cơ sở dữ liệu...")
        call_command("migrate", interactive=False)
        self.stdout.write(self.style.SUCCESS("✓ Cơ sở dữ liệu đã đồng bộ migrations."))

        # 2. Tạo 4 nhóm vai trò
        self.stdout.write("2. Khởi tạo 4 nhóm vai trò hệ thống...")
        groups = {}
        for role_name in ROLE_MENUS:
            group, created = Group.objects.get_or_create(name=role_name)
            groups[role_name] = group
            if created:
                self.stdout.write(f"  + Tạo mới nhóm: {role_name}")
            else:
                self.stdout.write(f"  ✓ Nhóm đã tồn tại: {role_name}")

        # Gán quyền mặc định cho từng nhóm
        training_types = ContentType.objects.filter(app_label="training")
        auth_types = ContentType.objects.filter(app_label="auth")

        # Quản trị viên: toàn quyền auth và training
        admin_perms = Permission.objects.filter(content_type__in=list(training_types) + list(auth_types))
        groups["Quản trị viên"].permissions.set(admin_perms)

        # Giáo vụ: toàn quyền quản lý đào tạo
        giaovu_perms = Permission.objects.filter(
            content_type__in=training_types,
            codename__in=[
                "add_course", "change_course", "view_course",
                "add_trainingclass", "change_trainingclass", "view_trainingclass",
                "add_teacher", "change_teacher", "view_teacher",
                "add_learner", "change_learner", "view_learner",
                "add_classassignment", "change_classassignment", "view_classassignment",
                "add_enrollment", "change_enrollment", "view_enrollment",
            ],
        )
        groups["Giáo vụ"].permissions.set(giaovu_perms)

        # Giáo viên: xem thông tin lớp
        giaovien_perms = Permission.objects.filter(
            content_type__in=training_types,
            codename__in=["view_trainingclass", "view_course"],
        )
        groups["Giáo viên"].permissions.set(giaovien_perms)

        self.stdout.write(self.style.SUCCESS("✓ Đã cấu hình quyền hạn cho 4 nhóm vai trò."))

        # 3. Tạo / Cập nhật tài khoản Superuser mặc định
        user_model = get_user_model()
        username = os.environ.get("BOOTSTRAP_SUPERUSER_USERNAME", "cce_admin")
        password = os.environ.get("BOOTSTRAP_SUPERUSER_PASSWORD", "CCEtest@2026!")
        email = os.environ.get("BOOTSTRAP_SUPERUSER_EMAIL", "admin@cce.edu.vn")

        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": "Quản trị viên",
                "last_name": "Hệ thống",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()
        user.groups.add(groups["Quản trị viên"])

        if created:
            self.stdout.write(self.style.SUCCESS(f"✓ Tạo mới tài khoản quản trị: {username} (Mật khẩu: {password})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Cập nhật tài khoản quản trị: {username} (Mật khẩu: {password})"))

        # 4. Tùy chọn nạp dữ liệu mẫu
        if options.get("demo"):
            self.stdout.write("4. Nạp dữ liệu mẫu (--demo)...")
            call_command("seed_demo_data")
            self.stdout.write(self.style.SUCCESS("✓ Dữ liệu mẫu đã được nạp thành công."))
        else:
            self.stdout.write("4. Bỏ qua nạp dữ liệu mẫu (sử dụng cờ --demo nếu cần).")

        self.stdout.write(self.style.SUCCESS("=== KHỞI TẠO HOÀN TẤT THÀNH CÔNG ==="))
