import os
from .base import *

DEBUG = True

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "cce-insecure-dev-key-for-local-testing-only-replace-in-production-123456",
)

ALLOWED_HOSTS = ["*"]

