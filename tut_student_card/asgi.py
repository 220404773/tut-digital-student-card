"""
ASGI config for tut_student_card project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tut_student_card.settings')

application = get_asgi_application()
