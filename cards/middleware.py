import io
import logging

from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from tut_student_card.client_ip import get_client_ip

from .models import PageVisit

logger = logging.getLogger(__name__)

EXCLUDED_PREFIXES = ('/static/', '/media/', '/admin/')
EXCLUDED_PATHS = ('/favicon.ico',)
# Requests the pages make by themselves, not pages anyone opened: the live
# card's QR image refreshes every 30 seconds, and the idle-timeout check
# keeps the session alive. Counting them would swamp the visit figures.
EXCLUDED_URL_NAMES = ('card_qr_image', 'session_keepalive')


def purge_old_activity_daily():
    """Runs the retention clean-up (purge_old_activity) at most once a day,
    from the first request after midnight - so the POPIA retention period is
    enforced without a separate scheduled job. The cache key is shared by all
    workers (DatabaseCache), so only one of them runs it."""
    if not cache.add(f'activity-purge:{timezone.localdate()}', True, 60 * 60 * 25):
        return
    try:
        call_command('purge_old_activity', stdout=io.StringIO())
    except Exception:
        logger.exception("Daily activity clean-up failed")


class PageVisitMiddleware:
    """Logs page views for the admin reporting page's site-visit count.

    Only successful (200) GET responses are logged - a request that gets
    redirected (e.g. bounced to /login/ because the visitor wasn't
    authenticated) never actually showed them a page, so it shouldn't count
    as a visit to that destination.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        url_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
        if (
            request.method == 'GET'
            and response.status_code == 200
            and not request.path.startswith(EXCLUDED_PREFIXES)
            and request.path not in EXCLUDED_PATHS
            and url_name not in EXCLUDED_URL_NAMES
        ):
            user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
            PageVisit.objects.create(
                user=user,
                path=request.path,
                ip_address=get_client_ip(request),
            )

        purge_old_activity_daily()
        return response
