import os
from pathlib import Path
from django.templatetags.static import static
from django.urls import reverse_lazy
from training.navigation import FEATURE_ICONS

# Build paths inside the project: BASE_DIR points to site1/ root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Application definition
INSTALLED_APPS = [
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'training.apps.TrainingConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'site1.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'training' / 'templates',
            BASE_DIR / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'training.context_processors.ui_navigation',
            ],
        },
    },
]

WSGI_APPLICATION = 'site1.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'vi'
TIME_ZONE = 'Asia/Ho_Chi_Minh'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'training' / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Design System Concept 3 "Chính Xác" (Precision & Clarity) Configuration for django-unfold
UNFOLD = {
    "THEME": "light",
    "SITE_TITLE": "Trung tâm Kỹ thuật Điện toán",
    "SITE_HEADER": "Hệ thống Quản lý Đào tạo, Thi và Cấp chứng chỉ",
    "SITE_SUBHEADER": "Trung tâm Kỹ thuật Điện toán - ĐH Bách Khoa TP.HCM",
    "SITE_SYMBOL": "school",
    "SITE_URL": "/",
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_HISTORY": True,
    "COLORS": {
        "primary": {
            "50": "#F1F7FA",
            "100": "#E2EEF4",
            "200": "#C6DDE8",
            "300": "#9CC4D7",
            "400": "#6FA4BE",
            "500": "#3D7898",
            "600": "#173B57",
            "700": "#102F46",
            "800": "#0D273B",
            "900": "#091D2D",
            "950": "#06131D",
        },
    },
    "STYLES": [
        lambda request: static("training/css/tokens.css") + "?v=roles-delete-ui-20261111",
        lambda request: static("training/css/cce-unfold.css") + "?v=roles-delete-ui-20261111",
    ],
    "SIDEBAR": {
        "show_search": False,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Bảng điều khiển",
                "separator": False,
                "collapsible": False,
                "items": [
                    {
                        "title": "Bảng điều khiển",
                        "icon": FEATURE_ICONS["dashboard"],
                        "link": reverse_lazy("dashboard"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo vụ" for g in request.user.groups.all())
                        ),
                    },
                ],
            },
            {
                "title": "Quản lý đào tạo",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Chương trình đào tạo",
                        "icon": FEATURE_ICONS["courses"],
                        "link": reverse_lazy("admin:training_course_changelist"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo vụ" for g in request.user.groups.all())
                        ),
                    },
                    {
                        "title": "Lớp học",
                        "icon": FEATURE_ICONS["classes"],
                        "link": reverse_lazy("admin:training_trainingclass_changelist"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo vụ" for g in request.user.groups.all())
                        ),
                    },
                    {
                        "title": "Giáo viên",
                        "icon": FEATURE_ICONS["teachers"],
                        "link": reverse_lazy("admin:training_teacher_changelist"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo vụ" for g in request.user.groups.all())
                        ),
                    },
                    {
                        "title": "Học viên",
                        "icon": FEATURE_ICONS["learners"],
                        "link": reverse_lazy("admin:training_learner_changelist"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo vụ" for g in request.user.groups.all())
                        ),
                    },
                ],
            },
            {
                "title": "Giảng dạy",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Lớp của tôi",
                        "icon": FEATURE_ICONS["my-classes"],
                        "link": reverse_lazy("my-class-list"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Giáo viên" for g in request.user.groups.all())
                        ),
                    },
                ],
            },
            {
                "title": "Quản lý khảo thí",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "",
                        "icon": FEATURE_ICONS["exam-periods"],
                        "link": "#",
                        "permission": lambda request: False,
                    },
                ],
            },
            {
                "title": "Quản lý hệ thống",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Tài khoản",
                        "icon": FEATURE_ICONS["accounts"],
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Quản trị viên" for g in request.user.groups.all())
                        ),
                    },
                    {
                        "title": "Phân quyền",
                        "icon": FEATURE_ICONS["permissions"],
                        "link": reverse_lazy("role-permissions"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Quản trị viên" for g in request.user.groups.all())
                        ),
                    },
                    {
                        "title": "Nhật ký thao tác",
                        "icon": FEATURE_ICONS["audit-log"],
                        "link": reverse_lazy("audit-log-list"),
                        "permission": lambda request: request.user.is_authenticated and (
                            request.user.is_superuser or any(g.name == "Quản trị viên" for g in request.user.groups.all())
                        ),
                    },
                ],
            },
            {
                "title": "",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Hồ sơ & Mật khẩu",
                        "icon": "manage_accounts",
                        "link": reverse_lazy("profile"),
                        "permission": lambda request: request.user.is_authenticated,
                    },
                ],
            },
        ],
    },
}
