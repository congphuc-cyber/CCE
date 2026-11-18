from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class DashboardCacheTests(TestCase):
    def test_dashboard_is_not_cached_and_requires_login_after_logout(self):
        user = User.objects.create_superuser(
            username="dashboard-cache-user",
            email="dashboard-cache@example.test",
            password="test-password",
        )
        self.client.force_login(user)

        dashboard_response = self.client.get(reverse("dashboard"))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn("no-store", dashboard_response["Cache-Control"])
        self.assertContains(
            dashboard_response,
            'window.addEventListener("pageshow"',
        )

        self.client.post(reverse("logout"))
        logged_out_response = self.client.get(reverse("dashboard"))

        self.assertRedirects(
            logged_out_response,
            f"{reverse('login')}?next={reverse('dashboard')}",
        )
