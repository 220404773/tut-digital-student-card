"""Slows down password guessing while limiting how far anyone can lock a
student out.

Only *failed* logins count, and they count per email *and* network:
  - After FAILURES_BEFORE_WAIT failures for one email from one network, that
    pair has to wait - 30 seconds, doubling with each further failure, up to
    15 minutes. Someone typing junk at a student's predictable
    {student_number}@tut4life.ac.za address from their own network only
    makes *themselves* wait; the student on their own network is unaffected.
  - Guessing spread across many networks is caught by a wider cap: once one
    email collects ACCOUNT_FAILURES_PER_HOUR failures from anywhere within a
    clock hour, it pauses for 15 minutes for everyone. A new pause can't
    start within 60 minutes of the last one starting, and later failures
    don't extend a pause. So someone with several networks can still trip it
    on purpose, but for at most 15 minutes in any 60 - the accepted residual
    risk, traded for capping guesses.
  - A successful login clears that email + network's count.

The wait is returned to the caller as a message, not slept on the server:
Gunicorn runs a single worker here, so a real delay would hold up every
other student's request too.

Counts live in Django's cache, which is the database (settings.CACHES), so
they are shared by every worker and survive a restart.
"""
import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

FAILURES_BEFORE_WAIT = 5
FIRST_WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 15 * 60
ACCOUNT_FAILURES_PER_HOUR = 50
ACCOUNT_PAUSE_SECONDS = 15 * 60


def _email_id(email):
    # Hashed so cache keys never hold a student's email address.
    return hashlib.sha256((email or '').strip().lower().encode('utf-8')).hexdigest()[:32]


def _pair_key(email, ip):
    return f'login_fail:{_email_id(email)}:{ip}'


def _account_count_key(email, now):
    # One counter per clock hour; it expires on its own.
    return f'login_fail_account:{_email_id(email)}:{now:%Y%m%d%H}'


def _account_pause_key(email):
    return f'login_pause_until:{_email_id(email)}'


def _account_pause_started_key(email):
    return f'login_pause_started:{_email_id(email)}'


def _seconds_until(timestamp):
    if not timestamp:
        return 0
    return max(0, round((timestamp - timezone.now()).total_seconds()))


def login_wait_seconds(email, ip):
    """Seconds this email + network must wait before trying again (0 = go ahead)."""
    pair = cache.get(_pair_key(email, ip)) or {}
    return max(_seconds_until(pair.get('wait_until')), _seconds_until(cache.get(_account_pause_key(email))))


def record_failed_login(email, ip):
    now = timezone.now()

    pair = cache.get(_pair_key(email, ip)) or {'count': 0}
    pair['count'] += 1
    if pair['count'] >= FAILURES_BEFORE_WAIT:
        doublings = min(pair['count'] - FAILURES_BEFORE_WAIT, 10)
        wait = min(FIRST_WAIT_SECONDS * 2 ** doublings, MAX_WAIT_SECONDS)
        pair['wait_until'] = now + timedelta(seconds=wait)
    # Forgotten after a quiet day, so an old typo doesn't count against anyone.
    cache.set(_pair_key(email, ip), pair, 24 * 60 * 60)

    count_key = _account_count_key(email, now)
    count = (cache.get(count_key) or 0) + 1
    cache.set(count_key, count, 60 * 60)
    if count >= ACCOUNT_FAILURES_PER_HOUR:
        # A new pause only if none has started in the last 60 minutes - even
        # across a clock-hour boundary - and a running pause is never
        # extended by later failures.
        last_started = cache.get(_account_pause_started_key(email))
        if last_started is None or now - last_started >= timedelta(hours=1):
            cache.set(_account_pause_started_key(email), now, 60 * 60)
            cache.set(_account_pause_key(email), now + timedelta(seconds=ACCOUNT_PAUSE_SECONDS), ACCOUNT_PAUSE_SECONDS)


def clear_failed_logins(email, ip):
    cache.delete(_pair_key(email, ip))
