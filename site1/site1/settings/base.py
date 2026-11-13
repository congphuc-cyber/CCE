import os
from pathlib import Path
from django.templatetags.static import static

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
    'captcha',
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
        lambda request: static("training/css/tokens.css") + "?v=shared-sidebar-20261111",
        lambda request: static("training/css/cce-core.css") + "?v=avatar-profile-gap-20261111",
        lambda request: static("training/css/cce-unfold.css") + "?v=centered-user-table-20261113",
    ],
}
