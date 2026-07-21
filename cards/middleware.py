from .models import PageVisit


EXCLUDED_PREFIXES = ('/static/', '/media/', '/admin/')
EXCLUDED_PATHS = ('/favicon.ico',)


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

        if (
            request.method == 'GET'
            and response.status_code == 200
            and not request.path.startswith(EXCLUDED_PREFIXES)
            and request.path not in EXCLUDED_PATHS
        ):
            user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
            PageVisit.objects.create(
                user=user,
                path=request.path,
                ip_address=request.META.get('REMOTE_ADDR'),
            )

        return response
