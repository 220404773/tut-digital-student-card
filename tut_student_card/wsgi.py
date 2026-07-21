"""
WSGI config for tut_student_card project.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tut_student_card.settings')

application = get_wsgi_application()
