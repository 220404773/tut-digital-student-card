from django.contrib import admin
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView


def service_worker(request):
    # Served at the site root (not /static/sw.js) so its default scope
    # covers the whole app - a service worker can only control pages under
    # the path it's served from, and PWA install requires it to cover
    # everything, not just /static/.
    path = finders.find('sw.js')
    with open(path, 'rb') as f:
        return HttpResponse(f.read(), content_type='application/javascript')


urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('cards/', include('cards.urls')),
    path('sw.js', service_worker, name='service_worker'),
    path('privacy/', TemplateView.as_view(template_name='legal/privacy.html'), name='privacy'),
    path('terms/', TemplateView.as_view(template_name='legal/terms.html'), name='terms'),
    path('cookies/', TemplateView.as_view(template_name='legal/cookies.html'), name='cookies'),
    path('', TemplateView.as_view(template_name='index.html'), name='home'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
