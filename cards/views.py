import functools

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django_ratelimit.decorators import ratelimit
from django.http import FileResponse, HttpResponse, JsonResponse
from django.db.models import Avg, Max, Min, Count, Q, F, ExpressionWrapper, DurationField
from django.utils import timezone
from datetime import timedelta
from accounts.models import StudentUser
from .models import StudentCard, CardActivity, SignupMetrics, OCRMetrics, PageVisit
from .utils import get_card_display_data, build_card_verification_url
from .google_wallet import build_google_wallet_save_url
import qrcode


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
    from accounts.views import confirmation_cooldown_remaining

    user = request.user
    try:
        student_card = StudentCard.objects.get(user=user)
    except StudentCard.DoesNotExist:
        student_card = None

    context = {
        'student_card': student_card,
        'is_verified': user.is_verified,
        'card_data': get_card_display_data(student_card) if student_card else None,
        'confirmation_cooldown_remaining': confirmation_cooldown_remaining(user) if not user.email_confirmed else 0,
    }
    return render(request, 'cards/dashboard.html', context)


@student_required
def view_card(request):
    """
    View the digital student card with all details.
    """
    user = request.user
    student_card = get_object_or_404(StudentCard, user=user)
    
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
def download_qr_code(request, format_type='png'):
    """
    Download QR code in PNG or PDF format.
    """
    user = request.user
    student_card = get_object_or_404(StudentCard, user=user)
    
    # Log activity
    CardActivity.objects.create(
        student_card=student_card,
        activity_type='downloaded',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )
    
    if student_card.qr_code:
        # Read through the FieldFile itself (which goes via the storage
        # backend's open()) rather than .path - S3Boto3Storage doesn't
        # support .path at all, only local FileSystemStorage does.
        return FileResponse(
            student_card.qr_code.open('rb'),
            content_type='image/png',
            as_attachment=True,
            filename=f"student_card_qr_{student_card.card_number}.png"
        )
    else:
        # Generate QR code on the fly
        qr_data = build_card_verification_url(student_card.card_number, request)
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        qr_image = qr.make_image(fill_color="black", back_color="white")
        
        response = HttpResponse(content_type='image/png')
        response['Content-Disposition'] = f'attachment; filename="student_card_qr_{student_card.card_number}.png"'
        qr_image.save(response, 'PNG')
        return response


@student_required
def card_activity_log(request):
    """
    View activity log for student's card.
    """
    user = request.user
    student_card = get_object_or_404(StudentCard, user=user)
    
    activities = CardActivity.objects.filter(student_card=student_card).order_by('-timestamp')[:50]
    
    context = {
        'student_card': student_card,
        'activities': activities,
    }
    return render(request, 'cards/activity_log.html', context)


@student_required
def verify_card(request):
    """
    Display full card details for visual verification (security check).
    """
    user = request.user
    student_card = get_object_or_404(StudentCard, user=user)
    
    # Log verification activity
    CardActivity.objects.create(
        student_card=student_card,
        activity_type='verified',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )
    
    card_data = get_card_display_data(student_card)
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(card_data)
    
    context = {
        'card': student_card,
        'card_data': card_data,
        'user': user,
    }
    return render(request, 'cards/verify.html', context)


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

    CardActivity.objects.create(
        student_card=student_card,
        activity_type='verified',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')
    )

    context = {
        'card': student_card,
        'user': student_card.user,
    }
    return render(request, 'cards/verify_public.html', context)


def get_client_ip(request):
    """Get client IP address from request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


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
    email_confirmation_stats = StudentUser.objects.filter(is_staff=False).aggregate(
        total_students=Count('id'),
        confirmed_count=Count('id', filter=Q(email_confirmed=True)),
    )
    confirmation_times = StudentUser.objects.filter(
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
    }

    # Site traffic
    total_visits = PageVisit.objects.count()
    distinct_visitors = PageVisit.objects.filter(user__isnull=False).values('user').distinct().count()

    # Recent signups
    recent_signups = SignupMetrics.objects.select_related('user').order_by('-created_at')[:10]

    # Recent OCR processes
    recent_ocr = OCRMetrics.objects.select_related('user').order_by('-created_at')[:10]

    context = {
        'signup_stats': signup_stats,
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
    students = StudentUser.objects.filter(is_staff=False).annotate(confirmation_duration=confirmation_duration)

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
        StudentUser.objects
        .filter(page_visits__isnull=False)
        .annotate(visit_count=Count('page_visits'), last_visit=Max('page_visits__timestamp'))
        .order_by('-last_visit')
    )

    return render(request, 'cards/report_visit_users.html', {'visitors': visitors})


@staff_member_required(login_url='login')
def report_user_visits(request, user_id):
    """Drill-down behind a specific user's row - their full website visit history."""
    visitor = get_object_or_404(StudentUser, pk=user_id)
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
    students = StudentUser.objects.filter(is_staff=False)
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
    student = get_object_or_404(StudentUser, pk=user_id)
    student_card = StudentCard.objects.filter(user=student).first()
    activities = CardActivity.objects.filter(student_card=student_card).order_by('-timestamp')[:20] if student_card else []

    return render(request, 'cards/staff_student_detail.html', {
        'student': student,
        'student_card': student_card,
        'card_data': get_card_display_data(student_card) if student_card else None,
        'activities': activities,
    })
