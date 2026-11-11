from django.contrib.auth import views as auth_views
from django.urls import path
from training import views as training
from training.admin import role_admin_site

urlpatterns = [
    path('admin/role-permissions/', training.role_permissions, name='role-permissions'),
    path('admin/audit-log/', training.audit_log_list, name='audit-log-list'),
    path(
        'admin/audit-log/<int:log_id>/',
        training.audit_log_detail,
        name='audit-log-detail',
    ),
    path('admin/', role_admin_site.urls),
    path('', training.get_home, name='home'),
    path(
        'login/',
        training.CCELoginView.as_view(template_name='training/login.html'),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('403/', training.permission_denied, name='forbidden'),
    path('dashboard/', training.dashboard, name='dashboard'),
    path('profile/', training.profile, name='profile'),
    path('my-classes/', training.my_class_list, name='my-class-list'),
    path('classes/<int:class_id>/', training.class_detail, name='class-detail'),
    path('classes/<int:class_id>/close/', training.close_class, name='class-close'),
    path('classes/<int:class_id>/enroll/', training.enroll_learner, name='class-enroll'),
    path('classes/<int:class_id>/teachers/', training.class_assignments, name='class-assignments'),
    path(
        'classes/<int:class_id>/teachers/<int:assignment_id>/remove/',
        training.remove_class_assignment,
        name='class-assignment-remove',
    ),
    path('ui-demo/', training.ui_demo, name='ui-demo'),
]

handler403 = 'training.views.permission_denied'
