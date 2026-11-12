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
    # Exam — Đợt thi (Pha 1)
    path('exam/periods/', training.exam_period_list, name='exam-period-list'),
    path('exam/periods/add/', training.exam_period_add, name='exam-period-add'),
    path('exam/periods/<int:pk>/edit/', training.exam_period_edit, name='exam-period-edit'),
    path('exam/periods/<int:pk>/toggle/', training.exam_period_toggle, name='exam-period-toggle'),
    # Exam — Hồ sơ đăng ký (Pha 1)
    path('exam/applications/', training.exam_application_list, name='exam-application-list'),
    path('exam/applications/import/', training.exam_application_import, name='exam-application-import'),
    path('exam/applications/deduplicate/', training.exam_application_deduplicate, name='exam-application-deduplicate'),
    # Exam — Phòng thi (Pha 2)
    path('exam/rooms/', training.exam_room_list, name='exam-room-list'),
    path('exam/rooms/<int:pk>/assign/', training.exam_room_assign, name='exam-room-assign'),
    # Exam — Điểm thi (Pha 3)
    path('exam/scores/', training.exam_score_list, name='exam-score-list'),
    path('exam/scores/<int:room_id>/entry/', training.exam_score_entry, name='exam-score-entry'),
    path('exam/scores/upload/', training.exam_score_upload, name='exam-score-upload'),
    path('exam/scores/publish/', training.exam_score_publish, name='exam-score-publish'),
    # Exam — MyBK + Thống kê (Pha 4)
    path('exam/mybk-export/', training.mybk_export, name='mybk-export'),
    path('exam/statistics/', training.exam_statistics, name='exam-statistics'),
    # Báo cáo giáo vụ (Pha 4)
    path('reports/', training.report_list, name='report-list'),
    path('reports/export/', training.report_export_excel, name='report-export-excel'),
    # Chứng nhận (phạm vi khác — stub)
    path('certificates/', training.certificate_list, name='certificate-list'),
    path('exam/certificates/', training.exam_certificate_list, name='exam-certificate-list'),
    path('ui-demo/', training.ui_demo, name='ui-demo'),
]

handler403 = 'training.views.permission_denied'
