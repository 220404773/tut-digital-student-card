from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.db import models
from .models import StudentUser
from .forms import CustomUserCreationForm, CustomUserChangeForm, LoginForm, StyledPasswordChangeForm
from .tokens import email_confirmation_token
from cards.models import StudentCard, CardActivity, SignupMetrics, OCRMetrics
from cards.utils import process_registration_proof, generate_student_card, parse_registration_end_date, registration_period_has_expired, registration_period_is_implausible, link_recent_anonymous_visits, hash_uploaded_file

OCR_PREVIEW_SESSION_KEY = 'ocr_preview_cache'

# The full OCR result carries fields (id_number, university_name,
# qualification, academic_year, document_classification_reason, ...) that
# signup() never reads back out of the cache - only these are actually
# consumed downstream. Session data is DB-backed by default, so caching the
# unfiltered result would persist those extra fields to the database for the
# life of the session, not just "the required derived fields".
_OCR_CACHE_FIELDS = ('error', 'student_number', 'full_name', 'email', 'period', 'confidence_score', 'quality_score', 'document_classification_confidence')


def _scoped_ocr_cache(ocr_result):
    return {key: ocr_result[key] for key in _OCR_CACHE_FIELDS if key in ocr_result}

# Minimum wait between confirmation emails to the same account - matches the
# ~60s resend cooldown most mail-sending flows use (GitHub, Google account
# recovery, etc.), long enough to cover normal delivery delay so a student
# can't spam themselves (and the mail provider) with repeat sends.
EMAIL_CONFIRMATION_COOLDOWN_SECONDS = 60


def _confirmation_cooldown_key(user):
    return f'email_confirmation_cooldown_{user.pk}'


def start_confirmation_cooldown(user):
    allowed_at = timezone.now() + timedelta(seconds=EMAIL_CONFIRMATION_COOLDOWN_SECONDS)
    cache.set(_confirmation_cooldown_key(user), allowed_at, EMAIL_CONFIRMATION_COOLDOWN_SECONDS)


def confirmation_cooldown_remaining(user):
    """Whole seconds left before another confirmation email can be sent to
    this user, or 0 if they're clear to send/resend now."""
    allowed_at = cache.get(_confirmation_cooldown_key(user))
    if not allowed_at:
        return 0
    return max(0, round((allowed_at - timezone.now()).total_seconds()))


def post_login_redirect(user):
    """Staff/admin accounts have no student card - send them to the admin
    reporting page instead of the student dashboard."""
    return redirect('performance_dashboard') if user.is_staff else redirect('dashboard')


def send_confirmation_email(user, request):
    """Email a link that proves the student controls the email address on
    their account - independent of the OCR document check, which only
    confirms a document was read successfully, not who's holding it."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_confirmation_token.make_token(user)
    confirm_path = reverse('confirm_email', args=[uid, token])
    confirm_url = request.build_absolute_uri(confirm_path)

    body = render_to_string('accounts/email_confirmation_email.html', {
        'user': user,
        'confirm_url': confirm_url,
    })
    send_mail(
        subject='Confirm your TUT Digital Student Card email',
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    start_confirmation_cooldown(user)


def delete_registration_proof(user):
    """Delete the uploaded proof of registration after processing (POPIA
    compliance) - goes through the storage backend's own API (not raw
    os.path/MEDIA_ROOT) so this also works when storage is S3/Spaces-backed,
    not just local disk. Must be called from every code path that saves a
    user with a registration_proof attached, not just the final one - a
    save() partway through signup() already uploads the file, so an early
    return without this call leaves it sitting in storage indefinitely.
    """
    if user.registration_proof:
        try:
            if user.registration_proof.storage.exists(user.registration_proof.name):
                user.registration_proof.storage.delete(user.registration_proof.name)
            user.registration_proof = None
            user.save()
        except Exception as e:
            print(f"Error deleting proof file: {str(e)}")


def split_full_name(full_name):
    """Split an extracted full name into first and last name parts."""
    if not full_name:
        return '', ''

    parts = full_name.split()
    if len(parts) == 1:
        return parts[0].title(), ''

    return parts[0].title(), ' '.join(parts[1:]).title()


@require_http_methods(['POST'])
@ratelimit(key='ip', rate='20/h', method='POST', block=False)
def preview_registration_proof(request):
    """Return OCR data for a registration proof without creating an account."""
    if getattr(request, 'limited', False):
        return JsonResponse({'success': False, 'message': 'Too many uploads - please wait a while and try again.'}, status=429)

    registration_proof = request.FILES.get('registration_proof')
    if not registration_proof:
        return JsonResponse({'success': False, 'message': 'No file uploaded.'}, status=400)

    if registration_proof.size > 5242880:
        return JsonResponse({'success': False, 'message': 'File size exceeds 5MB limit.'}, status=400)

    ext = registration_proof.name.split('.')[-1].lower()
    if ext not in CustomUserCreationForm.FILE_SIGNATURES:
        return JsonResponse({'success': False, 'message': 'Invalid file format. Allowed: PDF, JPG, PNG'}, status=400)

    registration_proof.seek(0)
    header = registration_proof.read(8)
    registration_proof.seek(0)
    if not any(header.startswith(sig) for sig in CustomUserCreationForm.FILE_SIGNATURES[ext]):
        return JsonResponse({'success': False, 'message': 'This file does not look like a valid PDF, JPG, or PNG.'}, status=400)

    try:
        ocr_start_time = timezone.now()
        ocr_result = process_registration_proof(registration_proof)
        ocr_duration = (timezone.now() - ocr_start_time).total_seconds()
        if isinstance(ocr_result, dict) and ocr_result.get('error') == 'invalid_pdf':
            return JsonResponse({'success': False, 'message': 'This does not look like a valid TUT proof of registration. Please upload the correct document.'}, status=400)

        if not ocr_result:
            return JsonResponse({'success': False, 'message': 'No extractable information was found in the uploaded document.'}, status=400)

        # Cache the result against this exact file's content so signup(), if
        # submitted with the same unmodified file, can reuse it instead of
        # running OCR (and the LLM call) a second time - that redundant
        # second pass used to roughly double signup latency.
        request.session[OCR_PREVIEW_SESSION_KEY] = {
            'hash': hash_uploaded_file(registration_proof),
            'result': _scoped_ocr_cache(ocr_result),
            'duration': ocr_duration,
        }

        # Allow file to proceed even if we can't extract details (OCR may not be available)
        # User can fill in details manually
        if not ocr_result.get('student_number') and not ocr_result.get('full_name'):
            return JsonResponse({
                'success': True,
                'document_type': ocr_result.get('document_type', 'TUT Proof of Registration'),
                'document_classification_confidence': ocr_result.get('document_classification_confidence', 0),
                'document_classification_reason': ocr_result.get('document_classification_reason', 'Please enter your details manually.'),
                'email': '',
                'student_number': '',
                'first_name': '',
                'last_name': '',
                'full_name': '',
                'confidence_score': ocr_result.get('confidence_score', 0.5),
            })

        first_name, last_name = split_full_name(ocr_result.get('full_name'))
        return JsonResponse({
            'success': True,
            'document_type': ocr_result.get('document_type', 'TUT Proof of Registration'),
            'document_classification_confidence': ocr_result.get('document_classification_confidence', 0),
            'document_classification_reason': ocr_result.get('document_classification_reason', ''),
            'email': ocr_result.get('email', ''),
            'student_number': ocr_result.get('student_number', ''),
            'first_name': first_name,
            'last_name': last_name,
            'full_name': ocr_result.get('full_name', ''),
            'confidence_score': ocr_result.get('confidence_score', 0),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='10/h', method='POST', block=False)
def signup(request):
    """
    Handle user registration and proof of registration upload.
    """
    if request.user.is_authenticated:
        return post_login_redirect(request.user)

    if request.method == 'POST' and getattr(request, 'limited', False):
        messages.error(request, 'Too many signup attempts from your network. Please wait a while and try again.')
        return render(request, 'accounts/signup.html', {'form': CustomUserCreationForm()})

    if request.method == 'POST':
        # Defaults to request-received time; overridden below with the
        # moment the student actually picked a file, if the browser sent
        # one - measures real end-to-end signup time instead of just this
        # request's backend processing time. A page refresh loses the JS
        # variable backing that hidden field, so an abandoned attempt is
        # never counted.
        signup_start_time = timezone.now()
        upload_start_raw = request.POST.get('upload_start_time')
        if upload_start_raw:
            try:
                from datetime import datetime as dt, timezone as dt_timezone
                signup_start_time = dt.fromtimestamp(int(upload_start_raw) / 1000, tz=dt_timezone.utc)
            except (ValueError, TypeError, OSError):
                pass

        form = CustomUserCreationForm(request.POST, request.FILES)
        
        if form.is_valid():
            user = form.save(commit=False)
            # AbstractUser requires a unique username; the form only collects an
            # email, so derive the username from it instead of leaving it blank
            # (which caused every second signup to collide on the unique constraint).
            user.username = user.email
            # registration_proof is a ModelForm field, so form.save() above
            # already attached the uploaded file to this instance - clear it
            # immediately. OCR reads directly from the in-memory upload
            # (registration_proof local var below), never from this field,
            # so leaving it set would upload the file to Spaces on the next
            # user.save() only to have delete_registration_proof() delete it
            # again moments later (POPIA) - a full upload+check+delete round
            # trip against S3 for a file that was never needed in storage.
            user.registration_proof = None
            # The form makes this checkbox mandatory, so reaching here means it
            # was ticked - record when, since that timestamp is the evidence of
            # consent, not the tick itself.
            user.consent_given_at = timezone.now()

            # Track file upload metrics
            file_upload_size_mb = 0
            ocr_start_time = None
            ocr_duration = 0
            
            # Process the uploaded registration proof with OCR
            if 'registration_proof' in request.FILES:
                registration_proof = request.FILES['registration_proof']
                file_upload_size_mb = registration_proof.size / (1024 * 1024)  # Convert to MB
                
                # Run OCR verification - reuse the live-preview result if this
                # is the same file the student already previewed, instead of
                # running OCR (and the LLM call) a second time.
                try:
                    cached_preview = request.session.get(OCR_PREVIEW_SESSION_KEY)
                    if cached_preview and cached_preview.get('hash') == hash_uploaded_file(registration_proof):
                        ocr_result = cached_preview['result']
                        ocr_duration = cached_preview.get('duration', 0)
                        ocr_end_time = timezone.now()
                        ocr_start_time = ocr_end_time - timedelta(seconds=ocr_duration)
                        del request.session[OCR_PREVIEW_SESSION_KEY]
                    else:
                        ocr_start_time = timezone.now()
                        ocr_result = process_registration_proof(registration_proof)
                        ocr_end_time = timezone.now()
                        ocr_duration = (ocr_end_time - ocr_start_time).total_seconds()

                    if isinstance(ocr_result, dict) and ocr_result.get('error') == 'invalid_pdf':
                        user.is_verified = False
                        messages.warning(request, 'This does not look like a valid TUT proof of registration. Please upload the correct document.')
                        return render(request, 'accounts/signup.html', {'form': form})
                    
                    if ocr_result and ocr_result.get('confidence_score', 0) >= 0.8:
                        if not ocr_result.get('student_number') and not ocr_result.get('full_name'):
                            user.is_verified = False
                            messages.warning(request, 'The uploaded file did not contain the required details.')
                            user.save()
                            delete_registration_proof(user)
                            return render(request, 'accounts/signup.html', {'form': form})

                        if not user.email and ocr_result.get('email'):
                            user.email = ocr_result.get('email')

                        # Most important check in this whole branch: a
                        # document can extract cleanly (high confidence,
                        # every field present) while still being for a
                        # registration period that's already over. Reject
                        # it explicitly here, before is_verified is ever set
                        # True - this is what stops a card from being
                        # issued already-expired (see StudentCard.refresh_status()).
                        registration_end_date = parse_registration_end_date(ocr_result.get('period'))
                        if registration_period_has_expired(registration_end_date):
                            user.is_verified = False
                            messages.warning(
                                request,
                                f"This proof of registration expired on {registration_end_date:%d %b %Y}. "
                                f"Please upload your current ({timezone.now().year}) proof of registration."
                            )
                            user.save()
                            delete_registration_proof(user)
                            return render(request, 'accounts/signup.html', {'form': form})
                        if registration_period_is_implausible(registration_end_date):
                            # Almost certainly a misread year (e.g. 2027
                            # instead of 2026), not a real multi-year
                            # registration - reject the same way rather than
                            # trust a date this far out.
                            user.is_verified = False
                            messages.warning(
                                request,
                                f"The registration end date on this document ({registration_end_date:%d %b %Y}) "
                                f"doesn't look right - please upload a clearer copy of your current proof of registration."
                            )
                            user.save()
                            delete_registration_proof(user)
                            return render(request, 'accounts/signup.html', {'form': form})

                        user.is_verified = True
                        if not user.student_number and ocr_result.get('student_number'):
                            user.student_number = ocr_result.get('student_number')
                        if ocr_result.get('full_name'):
                            first_name, last_name = split_full_name(ocr_result.get('full_name'))
                            if not user.first_name and first_name:
                                user.first_name = first_name
                            if not user.last_name and last_name:
                                user.last_name = last_name
                        user.registration_end_date = registration_end_date

                        messages.success(request, 'TUT Proof of Registration verified successfully!')
                    else:
                        user.is_verified = False
                        messages.warning(request, 'OCR verification partially matched. Admin review required.')
                except Exception as e:
                    user.is_verified = False
                    messages.warning(request, f'OCR processing error: {str(e)}. Please try again.')
            
            _t_save = timezone.now()
            user.save()
            print(f"user.save() took {(timezone.now() - _t_save).total_seconds():.2f}s")
            _t_delete = timezone.now()
            delete_registration_proof(user)
            print(f"delete_registration_proof() took {(timezone.now() - _t_delete).total_seconds():.2f}s")

            # Track signup metrics
            signup_end_time = timezone.now()
            signup_duration = (signup_end_time - signup_start_time).total_seconds()
            
            _t_signup_metrics = timezone.now()
            try:
                SignupMetrics.objects.create(
                    user=user,
                    signup_start_time=signup_start_time,
                    signup_end_time=signup_end_time,
                    signup_duration_seconds=signup_duration,
                    file_upload_size_mb=file_upload_size_mb,
                )
            except Exception as e:
                print(f"Error tracking signup metrics: {str(e)}")
            print(f"SignupMetrics.create() took {(timezone.now() - _t_signup_metrics).total_seconds():.2f}s")

            # Track OCR metrics if OCR was performed
            if ocr_result:
                _t_ocr_metrics = timezone.now()
                try:
                    confidence_percent = ocr_result.get('confidence_score', 0) * 100
                    OCRMetrics.objects.create(
                        user=user,
                        ocr_start_time=ocr_start_time,
                        ocr_end_time=ocr_end_time,
                        ocr_duration_seconds=ocr_duration,
                        ocr_confidence_score=confidence_percent,
                        extracted_student_number=ocr_result.get('student_number'),
                        extracted_full_name=ocr_result.get('full_name'),
                        is_accurate=user.is_verified,
                        # Accurate is >=80% (the auto-verify threshold, see
                        # is_verified above). 75-79% is close enough that a
                        # human should glance at it rather than write it off
                        # outright; below 75% is treated as a clean failure.
                        manual_correction_required=75 <= confidence_percent < 80,
                        document_quality_score=ocr_result.get('quality_score', 0) * 100,
                        document_classification_confidence=ocr_result.get('document_classification_confidence', 0) * 100,
                    )
                except Exception as e:
                    print(f"Error tracking OCR metrics: {str(e)}")
                print(f"OCRMetrics.create() took {(timezone.now() - _t_ocr_metrics).total_seconds():.2f}s")
            
            # Generate digital card if verified
            if user.is_verified:
                try:
                    card_gen_start = timezone.now()
                    generate_student_card(user, request)
                    card_gen_duration = (timezone.now() - card_gen_start).total_seconds()
                    print(f"Card generation (incl. QR upload) took {card_gen_duration:.2f}s for {user.email}")
                    messages.success(request, 'Your digital student card has been generated!')
                except Exception as e:
                    messages.error(request, f'Error generating card: {str(e)}')

            # Auto-login the user
            _t_login = timezone.now()
            login(request, user)
            print(f"login() took {(timezone.now() - _t_login).total_seconds():.2f}s")

            # Attribute their pre-signup page visits (e.g. viewing the signup
            # form) to the account that just got created.
            _t_link_visits = timezone.now()
            link_recent_anonymous_visits(user, request)
            print(f"link_recent_anonymous_visits() took {(timezone.now() - _t_link_visits).total_seconds():.2f}s")

            try:
                email_send_start = timezone.now()
                send_confirmation_email(user, request)
                email_send_duration = (timezone.now() - email_send_start).total_seconds()
                print(f"Confirmation email accepted by mail server for {user.email} (took {email_send_duration:.2f}s)")
                messages.info(request, 'Please check your email to confirm your address.')
            except Exception as e:
                print(f"Error sending confirmation email to {user.email}: {str(e)}")

            return redirect('dashboard')
        else:
            # An email or student number that's already registered means
            # this person almost certainly has an account already - send
            # them to log in instead of leaving them stuck on a failed
            # signup form.
            already_registered = any(
                'already registered' in str(error)
                for field in ('email', 'student_number')
                for error in form.errors.get(field, [])
            )
            if already_registered:
                messages.info(request, 'An account already exists with that email or student number. Please log in instead.')
                submitted_email = form.data.get('email', '')
                login_url = reverse('login')
                if submitted_email:
                    login_url = f"{login_url}?{urlencode({'email': submitted_email})}"
                return redirect(login_url)

            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'accounts/signup.html', {'form': form})


def password_reset_view(request):
    """Wraps Django's built-in PasswordResetView to redirect to signup after
    two failed attempts with an email that isn't registered.

    Note: this deliberately trades away the usual protection against email
    enumeration (a generic "if an account exists..." message regardless of
    whether it does) for a friendlier UX, per explicit product decision.
    """
    from accounts.urls import rate_limited_password_reset_view

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        exists = bool(email) and StudentUser.objects.filter(email__iexact=email, is_active=True).exists()

        if exists:
            request.session['password_reset_failed_attempts'] = 0
        else:
            attempts = request.session.get('password_reset_failed_attempts', 0) + 1
            request.session['password_reset_failed_attempts'] = attempts

            if attempts >= 2:
                request.session['password_reset_failed_attempts'] = 0
                messages.info(request, "We couldn't find an account with that email. Please sign up instead.")
                return redirect('signup')

    return rate_limited_password_reset_view(request)


def confirm_email(request, uidb64, token):
    """Landing page for the link sent by send_confirmation_email.

    Confirms only on POST (the visible "Confirm my email" button), not on
    the initial GET - many institutional mail systems (Microsoft Safe
    Links, Google Workspace, corporate security gateways) automatically
    pre-fetch links inside incoming emails to scan them for malware before
    the recipient ever opens the message. If a bare page visit confirmed
    the email, that automated scan would silently consume the one-time
    token itself.
    """
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = StudentUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, StudentUser.DoesNotExist):
        user = None

    token_valid = user is not None and email_confirmation_token.check_token(user, token)
    # The token's hash includes email_confirmed, so it goes invalid the
    # instant confirmation succeeds - including for a second, near-duplicate
    # submission of the same click (e.g. a double-click on the button, or
    # the browser retrying the POST). That's a *different* situation from a
    # genuinely bad/tampered/expired link, and deserves a different message
    # instead of the alarming default - the email was in fact confirmed.
    already_confirmed = not token_valid and user is not None and user.email_confirmed

    if request.method == 'POST':
        if token_valid:
            user.email_confirmed = True
            user.email_confirmed_at = timezone.now()
            user.save()
            messages.success(request, 'Your email address has been confirmed!')
        elif already_confirmed:
            messages.success(request, 'Your email address is already confirmed.')
        else:
            messages.error(request, 'This confirmation link is invalid or has expired. Request a new one from your dashboard.')

        if request.user.is_authenticated:
            return post_login_redirect(request.user)
        return redirect('login')

    return render(request, 'accounts/confirm_email.html', {'token_valid': token_valid, 'already_confirmed': already_confirmed})


@login_required(login_url='login')
@ratelimit(key='user', rate='5/h', method='POST', block=False)
def resend_confirmation_email(request):
    """Lets a student request a fresh confirmation link if the first one
    expired or never arrived."""
    if request.method == 'POST' and not getattr(request, 'limited', False):
        if not request.user.email_confirmed:
            remaining = confirmation_cooldown_remaining(request.user)
            if remaining > 0:
                messages.warning(request, f'Please wait {remaining}s before requesting another confirmation email.')
            else:
                try:
                    send_confirmation_email(request.user, request)
                    print(f"Confirmation email accepted by mail server for {request.user.email}")
                    messages.success(request, 'Confirmation email sent - please check your inbox.')
                except Exception as e:
                    print(f"Error sending confirmation email to {request.user.email}: {str(e)}")
                    messages.error(request, 'Could not send the confirmation email right now. Please try again later.')
    elif getattr(request, 'limited', False):
        messages.error(request, 'Too many resend attempts - please wait a while and try again.')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard')


@login_required(login_url='login')
@ratelimit(key='user', rate='10/h', method='POST', block=False)
def reupload_proof(request):
    """Lets an already-registered student upload a fresh proof of
    registration to reactivate an expired card (or complete verification if
    they never got one) - the self-service half of card expiry, the way a
    corporate domain account lets you change your own password once it's
    expired rather than needing IT to do it for you. Staff-side, the
    equivalent action is the Reissue button on the Expired Cards report.
    """
    if request.user.is_staff:
        return redirect('performance_dashboard')

    if request.method != 'POST':
        return render(request, 'accounts/reupload_proof.html')

    if getattr(request, 'limited', False):
        messages.error(request, 'Too many attempts - please wait a while and try again.')
        return redirect('reupload_proof')

    registration_proof = request.FILES.get('registration_proof')
    if not registration_proof:
        messages.warning(request, 'Please choose a file to upload.')
        return redirect('reupload_proof')

    if registration_proof.size > 5242880:
        messages.warning(request, 'File size exceeds 5MB limit.')
        return redirect('reupload_proof')

    ext = registration_proof.name.split('.')[-1].lower()
    if ext not in CustomUserCreationForm.FILE_SIGNATURES:
        messages.warning(request, 'Invalid file format. Allowed: PDF, JPG, PNG')
        return redirect('reupload_proof')

    registration_proof.seek(0)
    header = registration_proof.read(8)
    registration_proof.seek(0)
    if not any(header.startswith(sig) for sig in CustomUserCreationForm.FILE_SIGNATURES[ext]):
        messages.warning(request, 'This file does not look like a valid PDF, JPG, or PNG.')
        return redirect('reupload_proof')

    try:
        ocr_result = process_registration_proof(registration_proof)
    except Exception as e:
        messages.warning(request, f'Could not process this document: {str(e)}. Please try again.')
        return redirect('reupload_proof')

    if isinstance(ocr_result, dict) and ocr_result.get('error') == 'invalid_pdf':
        messages.warning(request, 'This does not look like a valid TUT proof of registration. Please upload the correct document.')
        return redirect('reupload_proof')

    if not ocr_result or ocr_result.get('confidence_score', 0) < 0.8:
        messages.warning(request, 'This document could not be verified with enough confidence. Please upload a clearer copy of your current proof of registration.')
        return redirect('reupload_proof')

    registration_end_date = parse_registration_end_date(ocr_result.get('period'))
    if registration_period_has_expired(registration_end_date):
        messages.warning(
            request,
            f"This proof of registration expired on {registration_end_date:%d %b %Y}. "
            f"Please upload your current ({timezone.now().year}) proof of registration."
        )
        return redirect('reupload_proof')
    if registration_period_is_implausible(registration_end_date):
        messages.warning(
            request,
            f"The registration end date on this document ({registration_end_date:%d %b %Y}) doesn't look right - "
            f"please upload a clearer copy of your current proof of registration."
        )
        return redirect('reupload_proof')

    user = request.user
    user.registration_end_date = registration_end_date
    user.is_verified = True
    user.save()

    student_card = StudentCard.objects.filter(user=user).first()
    if student_card:
        student_card.expiry_date = registration_end_date
        student_card.status = 'active'
        student_card.save()
        CardActivity.objects.create(student_card=student_card, activity_type='reactivated')
        messages.success(request, 'Your digital student card has been reactivated!')
    else:
        try:
            generate_student_card(user, request)
            messages.success(request, 'Your digital student card has been generated!')
        except Exception as e:
            messages.error(request, f'Error generating card: {str(e)}')

    return redirect('dashboard')


@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='10/m', method='POST', block=False)
def login_view(request):
    """
    Handle user login. Errors are attached to the specific field (email vs
    password) that caused the failure so the template can highlight it,
    instead of a single generic "login failed" message.
    """
    if request.user.is_authenticated:
        return post_login_redirect(request.user)

    form = LoginForm(request.POST or None, initial={'email': request.GET.get('email', '')})

    if request.method == 'POST' and getattr(request, 'limited', False):
        messages.error(request, 'Too many login attempts from your network. Please wait a minute and try again.')
        return render(request, 'accounts/login.html', {'form': form})

    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        password = form.cleaned_data['password']

        try:
            # Emails are case-insensitive in practice (e.g. TUT student mail),
            # so match the same way regardless of how it was typed at signup.
            matched_user = StudentUser.objects.get(email__iexact=email)
        except StudentUser.DoesNotExist:
            form.add_error('email', 'No account found with this email address.')
        else:
            authenticated_user = authenticate(request, username=matched_user.username, password=password)
            if authenticated_user is not None:
                login(request, authenticated_user)
                messages.success(request, f'Welcome back, {authenticated_user.first_name}!')
                return post_login_redirect(authenticated_user)
            form.add_error('password', 'Incorrect password.')

    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    """
    Handle user logout. Deliberately not @login_required - the idle-timeout
    modal in base.html navigates here once the client-side timer expires,
    which can be after the session has already expired server-side too;
    logout() on an already-anonymous request is a harmless no-op, and this
    way the "signed out due to inactivity" message still shows either way.
    """
    logout(request)
    if request.GET.get('reason') == 'idle':
        messages.info(request, "You've been signed out after a period of inactivity.")
    return redirect('home')


@login_required(login_url='login')
def session_keepalive(request):
    """Hit by the idle-timeout warning modal's "Stay signed in" button.
    Doesn't need to do anything itself - SESSION_SAVE_EVERY_REQUEST means
    just being a logged-in request resets the session's expiry clock.
    Returns the fresh expiry so the client can restart its own timers
    without a full page reload.
    """
    return JsonResponse({'expires_in': request.session.get_expiry_age()})


@login_required(login_url='login')
def profile(request):
    """
    Display and edit user profile, and let the student change their own
    password. Two independent forms share this page, distinguished by
    which submit button was clicked.
    """
    user = request.user

    if request.method == 'POST' and request.POST.get('form_type') == 'password':
        form = CustomUserChangeForm(instance=user)
        password_form = StyledPasswordChangeForm(user, request.POST)
        if password_form.is_valid():
            updated_user = password_form.save()
            # Keep the user logged in - changing the password would
            # otherwise invalidate their current session.
            update_session_auth_hash(request, updated_user)
            messages.success(request, 'Password changed successfully!')
            return redirect('profile')
    elif request.method == 'POST':
        form = CustomUserChangeForm(request.POST, request.FILES, instance=user)
        password_form = StyledPasswordChangeForm(user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully!')
            return redirect('dashboard')
    else:
        form = CustomUserChangeForm(instance=user)
        password_form = StyledPasswordChangeForm(user)

    # Get user's student card
    try:
        student_card = StudentCard.objects.get(user=user)
    except StudentCard.DoesNotExist:
        student_card = None

    context = {
        'form': form,
        'password_form': password_form,
        'student_card': student_card,
        'is_verified': user.is_verified,
    }
    return render(request, 'accounts/profile.html', context)


def user_search(request):
    """
    Search for verified students.
    """
    query = request.GET.get('q', '')
    results = []
    
    if query:
        results = StudentUser.objects.filter(
            is_verified=True
        ).filter(
            models.Q(first_name__icontains=query) |
            models.Q(last_name__icontains=query) |
            models.Q(student_number__icontains=query) |
            models.Q(email__icontains=query)
        )
    
    return render(request, 'accounts/search.html', {
        'query': query,
        'results': results,
    })


def student_detail(request, student_id):
    """
    View a student's public profile.
    """
    student = get_object_or_404(StudentUser, pk=student_id, is_verified=True)
    try:
        student_card = StudentCard.objects.get(user=student)
    except StudentCard.DoesNotExist:
        student_card = None
    
    return render(request, 'accounts/student_detail.html', {
        'student': student,
        'student_card': student_card,
    })
