import functools
import logging

from django.conf import settings

from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from django.http import HttpResponse, HttpResponseNotAllowed
from django.db.models import Avg, Max, Min, Count, Q, F, ExpressionWrapper, DurationField
from django.utils import timezone
from django.utils.crypto import get_random_string
from datetime import timedelta
from accounts.models import User, DeletedStudentLog, ReviewRequest
from accounts.face_checks import compare_faces, describe_similarity
from accounts.notices import notify_student, show_new_notices
from accounts.photo_changes import notify_decision
from accounts.services import confirmation_cooldown_remaining, send_confirmation_email, split_full_name
from .models import StudentCard, CardActivity, SignupMetrics, OCRMetrics, PageVisit
from .utils import (
    get_card_display_data, build_card_verification_url,
    issue_card_if_allowed, live_code_status, registration_period_has_expired, registration_period_is_implausible,
    render_card_qr_png,
)
from .google_wallet import build_google_wallet_save_url
from .signals import sync_wallet_for_student
from tut_student_card.client_ip import get_client_ip

logger = logging.getLogger(__name__)


def student_required(view_func):
    """Staff/admin accounts have no student card - keep them off student-only
    pages (they'd otherwise see "verification pending" banners or 404s)."""
    @login_required(login_url='login')
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_staff:
            return redirect('performance_dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


@student_required
def dashboard(request):
    """
    Student dashboard showing their digital card.
    """

    user = request.user
    show_new_notices(request, user)
    try:
        student_card = StudentCard.objects.get(user=user)
        student_card.refresh_status()
    except StudentCard.DoesNotExist:
        student_card = None

    google_wallet_save_url = None
    if student_card:
        verification_url = build_card_verification_url(student_card.card_number, request)
        google_wallet_save_url = build_google_wallet_save_url(student_card, verification_url)

    context = {
        'student_card': student_card,
        'is_verified': user.is_verified,
        'card_data': get_card_display_data(student_card) if student_card else None,
        'confirmation_cooldown_remaining': confirmation_cooldown_remaining(user) if not user.email_confirmed else 0,
        'google_wallet_save_url': google_wallet_save_url,
        # What the student card office has done on this account, newest first.
        'notices': user.notices.all()[:5],
        # Most recent human-review request, so an unverified student sees
        # where their registration stands instead of a generic "pending".
        'latest_review': None if user.is_verified else (
            ReviewRequest.objects.filter(user=user).exclude(reason__in=ReviewRequest.CARD_CHANGE_REASONS).first()
        ),
        # The card waits for a photo (settings.REQUIRE_PHOTO_FOR_CARD).
        'photo_needed': settings.REQUIRE_PHOTO_FOR_CARD and not user.profile_picture,
        'live_qr_refresh_seconds': settings.LIVE_QR_REFRESH_SECONDS,
    }
    return render(request, 'cards/dashboard.html', context)


@student_required
def view_card(request):
    """
    View the digital student card with all details.
    """
    user = request.user
    student_card = get_object_or_404(StudentCard, user=user)
    student_card.refresh_status()

    # Log activity
    CardActivity.objects.create(
        student_card=student_card,
        activity_type='viewed',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )
    
    card_data = get_card_display_data(student_card)
    verification_url = build_card_verification_url(student_card.card_number, request)

    context = {
        'card': student_card,
        'card_data': card_data,
        'user': user,
        'google_wallet_save_url': build_google_wallet_save_url(student_card, verification_url),
    }
    return render(request, 'cards/card.html', context)


@student_required
def card_qr_image(request):
    """The signed-in student's own live QR code, shown on the dashboard and
    redrawn every LIVE_QR_REFRESH_SECONDS by static/js/live-card.js. Only
    ever their own card - there is no card number in the URL."""
    student_card = get_object_or_404(StudentCard, user=request.user)
    response = HttpResponse(render_card_qr_png(student_card.card_number, request, live=True), content_type='image/png')
    # Never cached: each one carries a code that expires, and after a reissue
    # the old card must disappear at once.
    response['Cache-Control'] = 'no-store'
    return response


@student_required
def download_qr_code(request, format_type='png'):
    """Download the student's QR code as a PNG file."""
    student_card = get_object_or_404(StudentCard, user=request.user)

    CardActivity.objects.create(
        student_card=student_card,
        activity_type='downloaded',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )

    response = HttpResponse(render_card_qr_png(student_card.card_number, request), content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="student_card_qr_{student_card.card_number}.png"'
    return response


@ratelimit(key='ip', rate='60/m', method='GET', block=False)
def verify_card_public(request, card_number):
    """
    Public card lookup that the QR code itself links to - lets campus staff
    (or anyone) scan a card and see whether it's valid, without needing to
    be logged in as that student. Deliberately omits contact details like
    email that the authenticated verify_card view shows.

    Rate-limited per IP - it's a public, unauthenticated endpoint, so
    without this it would be an easy way to script through card numbers.
    """
    if getattr(request, 'limited', False):
        return HttpResponse('Too many requests - please wait a moment and try again.', status=429)

    student_card = get_object_or_404(StudentCard, card_number=card_number)
    student_card.refresh_status()

    CardActivity.objects.create(
        student_card=student_card,
        activity_type='verified',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )

    context = {
        'card': student_card,
        'user': student_card.user,
        # Whether the scanned code came from the live card on a phone, or is
        # a screenshot, download or Wallet pass (see live_code_status).
        'code_status': live_code_status(card_number, request.GET.get('live')),
        'checked_at': timezone.localtime(),
    }
    return render(request, 'cards/verify_public.html', context)


def _photo_change_stats():
    """How photo changes on issued cards were decided, and how often the
    face comparison was used."""
    decisions = ReviewRequest.objects.filter(reason='photo_change')
    undo_records = decisions.filter(message='Automatic approval undone')
    by_staff = decisions.filter(resolved_by__isnull=False).exclude(pk__in=undo_records)
    staff_approved = by_staff.filter(status='approved')
    staff_rejected = by_staff.filter(status='declined')
    automatic = decisions.filter(resolved_by__isnull=True, status='approved')
    total = automatic.count() + by_staff.count()
    compared = decisions.exclude(pk__in=undo_records).filter(similarity__isnull=False)

    return {
        'total': total,
        'waiting': students_with_photo_changes().count(),
        'automatic': automatic.count(),
        'automatic_rate': automatic.count() * 100 / total if total else 0,
        'undone': undo_records.count(),
        'staff_approved': staff_approved.count(),
        'staff_rejected': staff_rejected.count(),
        'compared': compared.count(),
        'compared_rate': compared.count() * 100 / total if total else 0,
        'threshold': settings.FACE_AUTO_APPROVE_MIN_SIMILARITY,
        'comparison_on': settings.FACE_COMPARISON_ENABLED,
        'automatic_on': settings.FACE_COMPARISON_ENABLED and settings.FACE_AUTO_APPROVE_ENABLED,
        'recent': decisions.select_related('user', 'resolved_by').order_by('-resolved_at')[:10],
    }


@staff_member_required(login_url='login')
def performance_dashboard(request):
    """
    Admin dashboard showing system performance metrics.
    Available to staff/admin users only.
    """
    # Signup metrics
    signup_stats = SignupMetrics.objects.aggregate(
        total_signups=Count('id'),
        avg_signup_time=Avg('signup_duration_seconds'),
        min_signup_time=Min('signup_duration_seconds'),
        max_signup_time=Max('signup_duration_seconds'),
        avg_file_size=Avg('file_upload_size_mb'),
        verified_count=Count('id', filter=Q(user__is_verified=True)),
    )

    # Share of signups that OCR auto-verified without needing manual review.
    if signup_stats['total_signups'] > 0:
        signup_stats['verification_rate'] = (signup_stats['verified_count'] / signup_stats['total_signups']) * 100
    else:
        signup_stats['verification_rate'] = 0
    
    # Email confirmation metrics
    confirmation_duration = ExpressionWrapper(
        F('email_confirmed_at') - F('date_joined'), output_field=DurationField()
    )
    email_confirmation_stats = User.objects.filter(is_staff=False).aggregate(
        total_students=Count('id'),
        confirmed_count=Count('id', filter=Q(email_confirmed=True)),
    )
    confirmation_times = User.objects.filter(
        is_staff=False, email_confirmed=True, email_confirmed_at__isnull=False
    ).annotate(confirmation_duration=confirmation_duration).aggregate(
        avg_confirmation_time=Avg('confirmation_duration'),
        min_confirmation_time=Min('confirmation_duration'),
        max_confirmation_time=Max('confirmation_duration'),
    )
    email_confirmation_stats.update(confirmation_times)
    if email_confirmation_stats['total_students'] > 0:
        email_confirmation_stats['confirmation_rate'] = (
            email_confirmation_stats['confirmed_count'] / email_confirmation_stats['total_students']
        ) * 100
    else:
        email_confirmation_stats['confirmation_rate'] = 0
    email_confirmation_stats['awaiting_count'] = (
        email_confirmation_stats['total_students'] - email_confirmation_stats['confirmed_count']
    )

    # OCR metrics
    ocr_stats = OCRMetrics.objects.aggregate(
        total_ocr=Count('id'),
        avg_ocr_time=Avg('ocr_duration_seconds'),
        min_ocr_time=Min('ocr_duration_seconds'),
        max_ocr_time=Max('ocr_duration_seconds'),
        avg_confidence=Avg('ocr_confidence_score'),
        avg_classification_confidence=Avg('document_classification_confidence'),
        accurate_count=Count('id', filter=Q(is_accurate=True)),
        needs_review_count=Count('id', filter=Q(manual_correction_required=True)),
    )

    # Calculate accuracy percentage
    if ocr_stats['total_ocr'] > 0:
        ocr_stats['accuracy_percentage'] = (ocr_stats['accurate_count'] / ocr_stats['total_ocr']) * 100
    else:
        ocr_stats['accuracy_percentage'] = 0

    ocr_stats['inaccurate_count'] = (
        ocr_stats['total_ocr'] - ocr_stats['accurate_count'] - ocr_stats['needs_review_count']
    )

    # Card metrics
    today = timezone.now().date()
    card_stats = {
        'active_cards': StudentCard.objects.filter(status='active').count(),
        'expiring_soon': StudentCard.objects.filter(
            status='active',
            expiry_date__gte=today,
            expiry_date__lte=today + timedelta(days=90),
        ).count(),
        # Counts of revoke/reissue events (not current card status) - this is
        # an audit-trail metric, so it should reflect how many times staff
        # took the action, not how many cards currently sit in that state.
        'revoked_count': CardActivity.objects.filter(activity_type='revoked').count(),
        'reissued_count': CardActivity.objects.filter(activity_type='reissued').count(),
        'deleted_students_count': DeletedStudentLog.objects.count(),
        # Same definition report_expired_cards() uses: already-flipped
        # 'expired' rows, plus 'active' rows nobody's read since their
        # expiry_date passed (refresh_status() hasn't self-healed them yet).
        'expired_cards_count': StudentCard.objects.filter(
            Q(status='expired') | Q(status='active', expiry_date__lt=today)
        ).count(),
        'pending_reviews_count': ReviewRequest.objects.filter(status='pending').count(),
        'pending_photo_changes_count': students_with_photo_changes().count(),
        'pending_name_changes_count': students_with_name_changes().count(),
    }
    # The tile's headline is everything waiting for a staff decision on the
    # review page, with the kinds listed underneath - registration reviews
    # alone read "0" while a photo change was waiting.
    card_stats['pending_total_count'] = (
        card_stats['pending_reviews_count'] + card_stats['pending_photo_changes_count']
        + card_stats['pending_name_changes_count']
    )

    photo_stats = _photo_change_stats()

    # Site traffic
    total_visits = PageVisit.objects.count()
    distinct_visitors = PageVisit.objects.filter(user__isnull=False).values('user').distinct().count()

    # Recent signups
    recent_signups = SignupMetrics.objects.select_related('user').order_by('-created_at')[:10]

    # Recent OCR processes
    recent_ocr = OCRMetrics.objects.select_related('user').order_by('-created_at')[:10]

    context = {
        'signup_stats': signup_stats,
        'photo_stats': photo_stats,
        'email_confirmation_stats': email_confirmation_stats,
        'ocr_stats': ocr_stats,
        'card_stats': card_stats,
        'total_visits': total_visits,
        'distinct_visitors': distinct_visitors,
        'recent_signups': recent_signups,
        'recent_ocr': recent_ocr,
    }

    return render(request, 'cards/performance_dashboard.html', context)


@staff_member_required(login_url='login')
def report_cards(request):
    """Drill-down behind the Active Student Cards / Expiring in 3 Months tiles."""
    filter_type = request.GET.get('filter', 'active')
    today = timezone.now().date()
    cards = StudentCard.objects.select_related('user').filter(status='active')

    if filter_type == 'expiring':
        title = 'Cards Expiring in the Next 3 Months'
        cards = cards.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90))
    else:
        title = 'Active Student Cards'

    return render(request, 'cards/report_cards.html', {
        'title': title,
        'cards': cards.order_by('expiry_date'),
    })


@staff_member_required(login_url='login')
def report_email_confirmation(request):
    """Drill-down behind the Email Confirmation tiles - who's confirmed,
    who isn't, and how long confirmed accounts took."""
    filter_type = request.GET.get('filter', 'all')
    confirmation_duration = ExpressionWrapper(
        F('email_confirmed_at') - F('date_joined'), output_field=DurationField()
    )
    students = User.objects.filter(is_staff=False).annotate(confirmation_duration=confirmation_duration)

    if filter_type == 'confirmed':
        title = 'Email Confirmed'
        students = students.filter(email_confirmed=True)
    elif filter_type == 'unconfirmed':
        title = 'Email Not Yet Confirmed'
        students = students.filter(email_confirmed=False)
    else:
        title = 'All Students - Email Confirmation Status'

    return render(request, 'cards/report_email_confirmation.html', {
        'title': title,
        'students': students.order_by('-date_joined'),
    })


@staff_member_required(login_url='login')
def report_signups(request):
    """Drill-down behind the signup performance tiles (total / avg time / avg file size / avg upload time)."""
    signups = SignupMetrics.objects.select_related('user').order_by('-created_at')
    return render(request, 'cards/report_signups.html', {'signups': signups})


@staff_member_required(login_url='login')
def report_ocr(request):
    """Drill-down behind the OCR performance tiles, optionally filtered to one status bucket."""
    filter_type = request.GET.get('filter')
    ocr_records = OCRMetrics.objects.select_related('user').order_by('-created_at')

    titles = {
        'accurate': 'Accurate OCR Extractions',
        'needs_review': 'OCR Extractions Needing Review',
        'inaccurate': 'Inaccurate OCR Extractions',
    }
    if filter_type == 'accurate':
        ocr_records = ocr_records.filter(is_accurate=True)
    elif filter_type == 'needs_review':
        ocr_records = ocr_records.filter(manual_correction_required=True)
    elif filter_type == 'inaccurate':
        ocr_records = ocr_records.filter(is_accurate=False, manual_correction_required=False)

    return render(request, 'cards/report_ocr.html', {
        'ocr_records': ocr_records,
        'title': titles.get(filter_type, 'OCR Processing Details'),
    })


@staff_member_required(login_url='login')
def report_card_activity(request):
    """Drill-down behind the Revoked/Reissued tiles - a basic staff-facing
    audit trail. Every CardActivity row is logged already; this is just the
    first screen to actually see them, optionally filtered to one type."""
    filter_type = request.GET.get('filter')
    activities = CardActivity.objects.select_related(
        'student_card__user', 'performed_by'
    ).order_by('-timestamp')

    titles = {
        'revoked': 'Revoked Cards',
        'reissued': 'Reissued Cards',
    }
    if filter_type in ('revoked', 'reissued'):
        activities = activities.filter(activity_type=filter_type)

    return render(request, 'cards/report_card_activity.html', {
        'activities': activities[:500],
        'title': titles.get(filter_type, 'All Card Activity'),
    })


@staff_member_required(login_url='login')
def report_deleted_students(request):
    """Staff-facing record of every deleted student account. The row itself
    is gone the moment StudentUserAdmin deletes it (cascades away the card,
    activity log, everything) - this reads the snapshot captured at that
    moment instead (see accounts/admin.py:_log_deletion)."""
    deleted = DeletedStudentLog.objects.select_related('deleted_by').order_by('-deleted_at')
    return render(request, 'cards/report_deleted_students.html', {
        'deleted': deleted[:500],
    })


@staff_member_required(login_url='login')
def reissue_deleted_student(request, log_id):
    """Recreates a deleted student's account from the snapshot captured at
    deletion time, and issues them a fresh card - the actual User row
    (and its old card/QR code) is long gone, so this is a genuine recreate,
    not a literal "undo delete". The new account gets an unusable password;
    a set-password email (the same flow/templates as ordinary password
    reset) is how the student actually gets back in.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # One transaction on a locked log entry: a double click, or two staff at
    # once, can't both restore, and a failure part-way leaves nothing behind.
    # The emails go out only after it commits.
    with transaction.atomic():
        entry = get_object_or_404(DeletedStudentLog.objects.select_for_update(), pk=log_id)

        if entry.restored_at:
            messages.warning(request, f'{entry.full_name} was already restored on {entry.restored_at:%Y-%m-%d %H:%M}.')
            return redirect('report_deleted_students')

        if User.objects.filter(Q(student_number=entry.student_number) | Q(email__iexact=entry.email)).exists():
            messages.error(request, f'An account with student number {entry.student_number} or email {entry.email} already exists - cannot restore.')
            return redirect('report_deleted_students')

        first_name, last_name = split_full_name(entry.full_name)
        student = User(
            email=entry.email,
            username=entry.email,
            student_number=entry.student_number,
            first_name=first_name,
            last_name=last_name,
            is_verified=True,  # staff explicitly restoring the account is itself the manual-verification step
            registration_end_date=entry.registration_end_date,
            # Restoring is a new verification event, not a resume - the student
            # must reconfirm control of the mailbox again regardless of whether
            # they'd confirmed it before being deleted.
        )
        # A random throwaway password, not set_unusable_password(): Django's
        # PasswordResetForm.get_users() explicitly excludes accounts with an
        # unusable password from ever receiving a reset email, which would
        # silently strand the student with no way back in.
        student.set_password(get_random_string(32))
        student.save()

        # The same card rule as every other path: the restored student has to
        # reconfirm their mailbox first, so the card usually arrives then.
        # A savepoint, so a failed card leaves the restored account in place.
        try:
            with transaction.atomic():
                if issue_card_if_allowed(student, request) == 'issued':
                    card = StudentCard.objects.filter(user=student).first()
                    CardActivity.objects.create(student_card=card, activity_type='reissued', performed_by=request.user)
        except Exception:
            logger.exception("Card generation failed while restoring student %s", student.pk)
            messages.warning(request, 'Account restored, but the card could not be generated. Use Reissue to try again.')

        entry.restored_at = timezone.now()
        entry.restored_by = request.user
        entry.save(update_fields=['restored_at', 'restored_by'])
        notify_student(student, 'Your account was restored by the student card office, with a new card number.',
                       level='info', request=request, email=False)

    try:
        reset_form = PasswordResetForm(data={'email': student.email})
        if reset_form.is_valid():
            reset_form.save(
                request=request,
                email_template_name='accounts/password_reset_email.html',
                subject_template_name='accounts/password_reset_subject.txt',
            )
        send_confirmation_email(student, request)
    except Exception:
        logger.exception("Emails failed while restoring student %s", student.pk)
        messages.warning(
            request,
            f'{student.get_full_name_display()} was restored, but the emails could not be sent. '
            'They can use "Forgot password" on the login page to get back in.'
        )
        return redirect('report_deleted_students')

    messages.success(request, f'Restored {student.get_full_name_display()} with a new card - a set-password email and a new confirmation link have been sent to {student.email}.')
    return redirect('report_deleted_students')


@staff_member_required(login_url='login')
def report_expired_cards(request):
    """Staff-facing list of cards whose registration period has passed -
    the soft-expire equivalent of the Deleted Students report (nothing is
    removed, StudentCard.refresh_status() just flips status on the same
    row). Catches both cards already flipped to 'expired' by a previous
    read, and ones still sitting as 'active' that nobody's viewed since
    their expiry_date passed - self-healing each on the way through so
    this list is always accurate, not just whatever happened to be touched.
    The Reissue button reuses the same admin action staff already use for
    a lost/stolen card - the User row here is very much still alive,
    unlike the Deleted Students case.
    """
    today = timezone.now().date()
    expired_cards = StudentCard.objects.filter(
        Q(status='expired') | Q(status='active', expiry_date__lt=today)
    ).select_related('user').order_by('expiry_date')
    for card in expired_cards:
        card.refresh_status()

    return render(request, 'cards/report_expired_cards.html', {
        'expired_cards': expired_cards,
    })


@staff_member_required(login_url='login')
def report_review_requests(request):
    """Students the automated check didn't verify, waiting for a person to
    decide (POPIA s71). Pending requests first, then recent decisions."""
    pending = ReviewRequest.objects.filter(status='pending').select_related('user').order_by('created_at')
    resolved = list(ReviewRequest.objects.exclude(status='pending').select_related('user', 'resolved_by')
                    .order_by('-resolved_at')[:50])
    # Automatic photo approvals a staff member can still undo: the student's
    # latest photo decision, while the photo it replaced is kept.
    undoable = set()
    for review in resolved:
        if (review.reason == 'photo_change' and review.status == 'approved' and review.resolved_by_id is None
                and review.user.previous_profile_picture):
            latest = (ReviewRequest.objects.filter(user=review.user, reason='photo_change')
                      .order_by('-resolved_at').values_list('pk', flat=True).first())
            if latest == review.pk:
                undoable.add(review.pk)
    return render(request, 'cards/report_review_requests.html', {
        'pending': pending,
        'resolved': resolved,
        'photo_changes': [(student, _photo_comparison(student)) for student in students_with_photo_changes()],
        'face_comparison_enabled': settings.FACE_COMPARISON_ENABLED,
        'face_auto_approve_enabled': settings.FACE_COMPARISON_ENABLED and settings.FACE_AUTO_APPROVE_ENABLED,
        'auto_approve_min_similarity': settings.FACE_AUTO_APPROVE_MIN_SIMILARITY,
        'undoable': undoable,
        'name_changes': students_with_name_changes(),
        'today': timezone.now().date(),
    })


def _photo_comparison(student):
    """Advice for the reviewing staff member on whether the waiting photo
    shows the same person as the card photo - only if the student consented
    and comparison is switched on. Uses the score worked out at upload; only
    photos uploaded before that was kept are compared here. No face
    measurements are stored (see accounts/face_checks.py)."""
    if not (settings.FACE_COMPARISON_ENABLED and student.pending_photo_compare_consent and student.profile_picture):
        return None
    if student.pending_photo_similarity is not None:
        return describe_similarity(student.pending_photo_similarity)
    try:
        with student.profile_picture.open('rb') as current, student.pending_profile_picture.open('rb') as new:
            return compare_faces(current, new)
    except OSError:
        logger.warning("Could not open photos of student %s for comparison", student.pk)
        return None


def students_with_photo_changes():
    return User.objects.exclude(pending_profile_picture='').exclude(pending_profile_picture__isnull=True).order_by('pk')


def students_with_name_changes():
    return User.objects.filter(name_change_requested_at__isnull=False).order_by('name_change_requested_at')


@staff_member_required(login_url='login')
@require_POST
def resolve_name_change(request, user_id):
    """Approve or reject a name change a student made after their card was
    issued. The name is printed on the card, so it only changes once a person
    has checked it against the university's records."""
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        messages.error(request, 'Choose Approve or Reject.')
        return redirect('report_review_requests')

    # Locked so two staff deciding at once, or the student withdrawing the
    # request meanwhile, can't both act: whoever is second sees it decided.
    with transaction.atomic():
        student = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        if not student.name_change_requested_at:
            messages.info(request, 'That name change has already been decided.')
            return redirect('report_review_requests')
        previous = student.get_full_name_display()
        if action == 'approve':
            student.first_name, student.last_name = student.pending_first_name, student.pending_last_name
            sync_wallet_for_student(student)  # the name is on the Wallet pass too
        requested = f'{student.pending_first_name} {student.pending_last_name}'.strip()
        student.pending_first_name = student.pending_last_name = ''
        student.name_change_requested_at = None
        student.save(update_fields=['first_name', 'last_name', 'pending_first_name', 'pending_last_name',
                                    'name_change_requested_at'])
        decision = ReviewRequest.record_card_change(
            student, 'name_change', action == 'approve', request.user,
            note=request.POST.get('staff_note', '').strip()[:1000], detail=f'{previous} -> {requested}',
        )
        notify_decision(decision, request)
    if action == 'approve':
        messages.success(request, f'Approved the name change - the card now shows {requested}.')
    else:
        messages.success(request, f'Rejected the name change to {requested} - the card keeps {student.get_full_name_display()}.')
    return redirect('report_review_requests')


@staff_member_required(login_url='login')
@require_POST
def resolve_photo_change(request, user_id):
    """Approve or reject a new photo a student uploaded after their card was
    issued. The card's photo is what a guard compares with the holder's face,
    so it only changes once a person has checked it is the same student."""
    action = request.POST.get('action')
    if action not in ('approve', 'reject'):
        messages.error(request, 'Choose Approve or Reject.')
        return redirect('report_review_requests')

    # Locked like resolve_name_change. Files are deleted only after the
    # change commits, so a failed save never leaves the card without a photo.
    with transaction.atomic():
        student = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        if not student.pending_profile_picture:
            messages.info(request, 'That photo change has already been decided.')
            return redirect('report_review_requests')
        storage = student.pending_profile_picture.storage
        similarity = student.pending_photo_similarity
        if action == 'approve':
            unused = [student.profile_picture.name, student.previous_profile_picture.name]
            student.profile_picture = student.pending_profile_picture.name
            # A person has now checked the face: automatic approval may be
            # used again next time, and there is nothing left to undo.
            student.photo_auto_approved_at = None
            student.previous_profile_picture = None
        else:
            unused = [student.pending_profile_picture.name]
        student.pending_profile_picture = None
        student.pending_photo_compare_consent = False
        student.pending_photo_similarity = None
        student.save(update_fields=['profile_picture', 'pending_profile_picture', 'pending_photo_compare_consent',
                                    'pending_photo_similarity', 'photo_auto_approved_at', 'previous_profile_picture'])
        decision = ReviewRequest.record_card_change(
            student, 'photo_change', action == 'approve', request.user,
            note=request.POST.get('staff_note', '').strip()[:1000], similarity=similarity,
        )
        for name in filter(None, unused):
            transaction.on_commit(lambda name=name: storage.delete(name))
        notify_decision(decision, request)
    if action == 'approve':
        messages.success(request, f"Approved {student.get_full_name_display()}'s new photo - it now shows on their card.")
    else:
        messages.success(request, f"Rejected {student.get_full_name_display()}'s new photo - their card keeps the current one.")
    return redirect('report_review_requests')


@staff_member_required(login_url='login')
@require_POST
def undo_automatic_photo_approval(request, review_id):
    """Put back the photo an automatic approval replaced - for a staff
    member who disagrees with the face comparison. Possible until the old
    photo is cleared (AUTO_APPROVED_PHOTO_UNDO_DAYS, or a later decision)."""
    with transaction.atomic():
        review = get_object_or_404(ReviewRequest.objects.select_for_update(), pk=review_id,
                                   reason='photo_change', status='approved', resolved_by__isnull=True)
        student = User.objects.select_for_update().get(pk=review.user_id)
        latest = ReviewRequest.objects.filter(user=student, reason='photo_change').order_by('-resolved_at').first()
        if latest != review or not student.previous_profile_picture:
            messages.info(request, 'That automatic approval can no longer be undone.')
            return redirect('report_review_requests')
        storage = student.profile_picture.storage
        unused = student.profile_picture.name
        student.profile_picture = student.previous_profile_picture.name
        student.previous_profile_picture = None
        # photo_auto_approved_at stays set, so the next change goes to a person.
        student.save(update_fields=['profile_picture', 'previous_profile_picture'])
        review.message = f'{review.message} - undone by {request.user.email}'
        review.save(update_fields=['message'])
        note = request.POST.get('staff_note', '').strip()[:1000]
        ReviewRequest.record_card_change(
            student, 'photo_change', False, request.user,
            note=note or 'The automatic approval was reversed by staff.', detail='Automatic approval undone',
            similarity=review.similarity,
        )
        notify_student(
            student,
            "The automatic approval of your new photo was reversed by staff, so your card shows your previous "
            "photo again." + (f' Reason: {note}' if note else ''),
            level='warning', request=request,
        )
        transaction.on_commit(lambda: storage.delete(unused))
    messages.success(request, f"Undone - {student.get_full_name_display()}'s card shows their previous photo again.")
    return redirect('report_review_requests')


@staff_member_required(login_url='login')
@require_POST
def resolve_review_request(request, review_id):
    """Approve or decline a review. Approving needs the registration end date
    staff confirmed with the university - the uploaded document is deleted
    after processing, so it can't be the source - and issues the card under
    the same email-confirmation rule as every other path.

    The whole decision is one transaction on a locked review row: two staff
    deciding at once can't both act, and if issuing the card fails nothing is
    saved, rather than leaving the student verified with the review pending.
    """
    from datetime import date

    action = request.POST.get('action')
    note = request.POST.get('staff_note', '').strip()[:1000]
    if action not in ('approve', 'decline'):
        messages.error(request, 'Choose Approve or Decline.')
        return redirect('report_review_requests')

    end_date = None
    if action == 'approve':
        try:
            end_date = date.fromisoformat(request.POST.get('registration_end_date', ''))
        except ValueError:
            messages.error(request, 'Enter the registration end date you confirmed with the university to approve.')
            return redirect('report_review_requests')
        if registration_period_has_expired(end_date):
            messages.error(request, 'That registration end date has already passed - the card would be expired on issue.')
            return redirect('report_review_requests')
        if registration_period_is_implausible(end_date):
            # Same rule as the document check: a registration period doesn't
            # run more than a year, so this is almost certainly a typo.
            messages.error(request, f'{end_date:%d %b %Y} is more than a year away - check the date and try again.')
            return redirect('report_review_requests')

    try:
        with transaction.atomic():
            review = (ReviewRequest.objects.select_for_update().select_related('user')
                      .filter(pk=review_id, status='pending').first())
            if review is None:
                messages.info(request, 'This review has already been decided.')
                return redirect('report_review_requests')
            student = review.user

            if action == 'approve':
                # Update only these fields: saving the whole loaded row could
                # overwrite changes made since it was read.
                User.objects.filter(pk=student.pk).update(is_verified=True, registration_end_date=end_date)
                # A card issued earlier (e.g. after an admin verification)
                # follows the confirmed date too, unless it was revoked.
                StudentCard.objects.filter(user=student).exclude(status='revoked').update(
                    expiry_date=end_date, status='active',
                )
                sync_wallet_for_student(student)  # update() skips the card's own signals
                outcome = issue_card_if_allowed(student, request)

            review.status = 'approved' if action == 'approve' else 'declined'
            review.staff_note = note
            review.resolved_at = timezone.now()
            review.resolved_by = request.user
            review.save()

            if action == 'approve':
                next_step = {
                    'issued': ' Your digital student card is ready on your dashboard.',
                    'awaiting_email': ' Confirm your email address to receive your digital student card.',
                    'awaiting_photo': ' Add a profile photo on your Profile page to receive your digital student card.',
                    'exists': f' Your card is now valid until {end_date:%d %b %Y}.',
                }.get(outcome, '')
                notify_student(student, 'Your registration has been verified by the student card office.' + next_step,
                               level='success', request=request)
            else:
                notify_student(
                    student,
                    'The student card office could not verify your registration.'
                    + (f' Reason: {note}' if note else '')
                    + ' You can upload your current proof of registration again from your dashboard.',
                    level='warning', request=request,
                )
    except Exception:
        logger.exception("Resolving review %s failed", review_id)
        messages.error(request, 'The decision could not be saved, so nothing was changed. Please try again.')
        return redirect('report_review_requests')

    if action == 'approve':
        result = {
            'issued': 'card issued',
            'awaiting_email': 'card will be issued once they confirm their email',
            'awaiting_photo': 'card will be issued once they add a profile photo',
            'exists': "their existing card now runs to the confirmed date",
        }.get(outcome, 'verified')
        messages.success(request, f'Approved {student.get_full_name_display()} - {result}.')
    else:
        messages.success(request, f'Declined the review for {student.get_full_name_display()}.')
    return redirect('report_review_requests')


@staff_member_required(login_url='login')
def report_visits(request):
    """Drill-down behind the Website Visits tile."""
    all_visits = PageVisit.objects.order_by('-timestamp')
    return render(request, 'cards/report_visits.html', {
        'visits': all_visits[:500],
        'total_count': all_visits.count(),
    })


@staff_member_required(login_url='login')
def report_visit_users(request):
    """Drill-down behind the Distinct Users tile - one row per user who has
    visited the site, with their visit count and most recent visit."""
    visitors = (
        User.objects
        .filter(page_visits__isnull=False)
        .annotate(visit_count=Count('page_visits'), last_visit=Max('page_visits__timestamp'))
        .order_by('-last_visit')
    )

    return render(request, 'cards/report_visit_users.html', {'visitors': visitors})


@staff_member_required(login_url='login')
def report_user_visits(request, user_id):
    """Drill-down behind a specific user's row - their full website visit history."""
    visitor = get_object_or_404(User, pk=user_id)
    visits = PageVisit.objects.filter(user=visitor).order_by('-timestamp')

    return render(request, 'cards/report_user_visits.html', {
        'visitor': visitor,
        'visits': visits,
    })


@staff_member_required(login_url='login')
def staff_student_list(request):
    """Staff-only directory of all students, so admins can find and drill
    into any student's profile and card without using the raw Django admin."""
    query = request.GET.get('q', '')
    students = User.objects.filter(is_staff=False)
    if query:
        students = students.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(student_number__icontains=query) |
            Q(email__icontains=query)
        )

    return render(request, 'cards/staff_student_list.html', {
        'students': students.order_by('-date_joined'),
        'query': query,
    })


@staff_member_required(login_url='login')
def staff_student_detail(request, user_id):
    """Staff-only read view of a single student's profile, card, and activity.
    Editing is delegated to the Django admin change form rather than
    duplicating a second edit UI."""
    student = get_object_or_404(User, pk=user_id)
    student_card = StudentCard.objects.filter(user=student).first()
    if student_card:
        student_card.refresh_status()
    activities = CardActivity.objects.filter(student_card=student_card).select_related('performed_by').order_by('-timestamp')[:20] if student_card else []

    return render(request, 'cards/staff_student_detail.html', {
        'student': student,
        'student_card': student_card,
        'card_data': get_card_display_data(student_card) if student_card else None,
        'activities': activities,
    })
