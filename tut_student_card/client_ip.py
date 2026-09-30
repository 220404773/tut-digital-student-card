"""The one place the app decides a request's client IP address - used for
rate limiting (RATELIMIT_IP_META_KEY), the card activity log and page visits.

Behind a hosting platform's load balancer, REMOTE_ADDR is the proxy's address,
so every student would look like the same client and share one rate limit.
The real address is in a header the proxy adds, but a client can send those
headers too, so only the part written by our own proxy is trusted:

  CLIENT_IP_HEADER     a header the proxy *replaces* with the client address
                       (e.g. DigitalOcean's DO-Connecting-IP). Preferred.
  TRUSTED_PROXY_COUNT  how many proxies *append* to X-Forwarded-For. The
                       client-supplied entries are on the left, so the address
                       is read that many places from the right, never the left.

With neither set (local development, no proxy) REMOTE_ADDR is used as-is.
"""
import ipaddress

from django.conf import settings


def _valid_ip(value):
    value = (value or '').strip()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return None
    return value


def get_client_ip(request):
    header = getattr(settings, 'CLIENT_IP_HEADER', '')
    if header:
        meta_key = 'HTTP_' + header.upper().replace('-', '_')
        ip = _valid_ip(request.META.get(meta_key))
        if ip:
            return ip

    proxy_count = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)
    if proxy_count > 0:
        forwarded = [part.strip() for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if part.strip()]
        if len(forwarded) >= proxy_count:
            ip = _valid_ip(forwarded[-proxy_count])
            if ip:
                return ip

    # Falls back here when the proxy header is missing or malformed, so a junk
    # value can never reach a GenericIPAddressField (PostgreSQL's inet column
    # rejects it, which would turn a bad header into a server error).
    return request.META.get('REMOTE_ADDR') or '0.0.0.0'
