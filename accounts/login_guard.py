"""Slows down password guessing without letting anyone lock a student out.

Only *failed* logins count, and they count per email *and* network:
  - After FAILURES_BEFORE_WAIT failures for one email from one network, that
    pair has to wait - 30 seconds, doubling with each further failure, up to
    15 minutes. Someone typing junk at a student's predictable
    {student_number}@tut4life.ac.za address from their own network only
    makes *themselves* wait; the student on their own network is unaffected.
  - Guessing spread across many networks is caught by a wider cap: once
    one email collects ACCOUNT_FAILURES_PER_HOUR failures from anywhere, it
    pauses for 15 minutes for everyone. That cap can still be tripped on
    purpose by someone with many networks - the accepted residual risk.
  - A successful login clears that email + network's count.

The wait is returned to the caller as a message, not slept on the server:
Gunicorn runs a single worker here, so a real delay would hold up every
other student's request too.

Counts live in Django's cache. With the default in-process cache that is
correct only while one worker serves the site - see README "Rate limiting".
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


def _account_key(email):
    return f'login_fail_account:{_email_id(email)}'


def _seconds_until(timestamp):
    if not timestamp:
        return 0
    return max(0, round((timestamp - timezone.now()).total_seconds()))


def login_wait_seconds(email, ip):
    """Seconds this email + network must wait before trying again (0 = go ahead)."""
    pair = cache.get(_pair_key(email, ip)) or {}
    account = cache.get(_account_key(email)) or {}
    return max(_seconds_until(pair.get('wait_until')), _seconds_until(account.get('wait_until')))


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

    account = cache.get(_account_key(email)) or {'count': 0}
    account['count'] += 1
    if account['count'] >= ACCOUNT_FAILURES_PER_HOUR:
        account['wait_until'] = now + timedelta(seconds=ACCOUNT_PAUSE_SECONDS)
    cache.set(_account_key(email), account, 60 * 60)


def clear_failed_logins(email, ip):
    cache.delete(_pair_key(email, ip))
