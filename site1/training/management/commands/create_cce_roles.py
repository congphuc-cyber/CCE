from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from training.navigation import ROLE_MENUS


class Command(BaseCommand):
    help = "Tạo các nhóm vai trò dùng chung cho hệ thống CCE."

    def handle(self, *args, **options):
        for role_name in ROLE_MENUS:
            _, created = Group.objects.get_or_create(name=role_name)
            if created:
                self.stdout.write(self.style.SUCCESS("Created CCE role group."))
            else:
                self.stdout.write("CCE role group already exists.")
