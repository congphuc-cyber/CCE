import os

# Default to dev settings unless DJANGO_ENV is 'production'
env = os.environ.get("DJANGO_ENV", "development").lower()
if env in ("prod", "production"):
    from .prod import *
else:
    from .dev import *

