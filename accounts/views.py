import logging
from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode
from django.db import models, transaction
from .models import ReviewRequest, User
from .forms import CustomUserCreationForm, CustomUserChangeForm, LoginForm, StyledPasswordChangeForm
from .login_guard import clear_failed_logins, login_wait_seconds, record_failed_login
from tut_student_card.client_ip import get_client_ip
from .services import (
    EMAIL_NOT_CONFIGURED_MESSAGE, confirmation_cooldown_remaining, email_is_delivered,
    delete_registration_proof, normalise_student_number, send_confirmation_email, split_full_name,
)
from .tokens import email_confirmation_token
from cards.models import StudentCard, CardActivity, SignupMetrics, OCRMetrics
from cards.utils import process_registration_proof, issue_card_if_allowed, parse_registration_end_date, registration_period_has_expired, registration_period_is_implausible, link_recent_anonymous_visits, hash_uploaded_file

logger = logging.getLogger(__name__)

OCR_PREVIEW_SESSION_KEY = 'ocr_preview_cache'

PHOTO_NEEDED_MESSAGE = (
    'Your registration is verified. Add a profile photo on your Profile page to receive your digital '
    'student card - the photo is what campus staff compare with your face.'
)

# The full OCR result carries fields (id_number, university_name,
# qualification, academic_year, document_classification_reason, ...) that
# signup() never reads back out of the cache - only these are actually
# consumed downstream. Session data is DB-backed by default, so caching the
# unfiltered result would persist those extra fields to the database for the
# life of the session, not just "the required derived fields".
_OCR_CACHE_FIELDS = ('error', 'student_number', 'full_name', 'email', 'period', 'confidence_score', 'quality_score', 'document_classification_confidence', 'needs_human_review')


def _scoped_ocr_cache(ocr_result):
    return {key: ocr_result[key] for key in _OCR_CACHE_FIELDS if key in ocr_result}


def post_login_redirect(user):
    """Staff/admin accounts have no student card - send them to the admin
    reporting page instead of the student dashboard."""
    return redirect('performance_dashboard') if user.is_staff else redirect('dashboard')


@require_http_methods(['POST'])
# Per-IP ceilings are set for a campus, where many students share one public
# IP address: generous enough for a class registering together, still low
# enough to stop one machine hammering the OCR and AI pipeline.
@ratelimit(key='ip', rate='120/h', method='POST', block=False)
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
    except Exception:
        logger.exception("Document preview failed")
        return JsonResponse({'success': False, 'message': 'This document could not be read. Please try again.'}, status=400)


@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='60/h', method='POST', block=False)
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
            student_number_mismatch = False
            # Set when the account should go to a staff member rather than be
            # decided by the automated check alone (POPIA s71) - see ReviewRequest.
            review_reason = None
            # Stay None if processing fails before OCR runs, so the metrics
            # step below skips it instead of crashing after the user is saved.
            ocr_result = None
            ocr_end_time = None

            # Process the uploaded registration proof with OCR
            if 'registration_proof' in request.FILES:
                registration_proof = request.FILES['registration_proof']
                file_upload_size_mb = registration_proof.size / (1024 * 1024)  # Convert to MB

                # One document, one account. The same file being submitted for
                # a second account means either a shared document or a reused
                # one, and neither should silently produce a second card.
                proof_hash = hash_uploaded_file(registration_proof)
                if User.objects.filter(registration_proof_hash=proof_hash).exists():
                    messages.warning(
                        request,
                        'This exact document has already been used to register another account. '
                        'Please upload your own proof of registration.'
                    )
                    return render(request, 'accounts/signup.html', {'form': form})

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
                        if not form.cleaned_data.get('request_review'):
                            # By default a rejected document creates no account
                            # (FR-03) - but the student can disagree with the
                            # automated decision and ask a person instead.
                            messages.warning(
                                request,
                                'This does not look like a valid TUT proof of registration. Please upload the correct document. '
                                "If you're sure it is your proof of registration, tick \"Ask a staff member to verify "
                                'my registration" below and submit again.'
                            )
                            return render(request, 'accounts/signup.html', {'form': form, 'offer_review': True})
                        review_reason = 'document_rejected'
                        messages.info(
                            request,
                            'Your account has been created without automatic verification. '
                            'A staff member will check your registration with the university.'
                        )

                    elif ocr_result and ocr_result.get('confidence_score', 0) >= 0.8:
                        # These three rejections create no account, like an
                        # invalid document above - the student fixes the
                        # document and submits again. (Saving one here left
                        # them with an account they weren't logged in to, and
                        # a retry said the email was already registered.)
                        if not ocr_result.get('student_number') and not ocr_result.get('full_name'):
                            messages.warning(request, 'The uploaded file did not contain the required details.')
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
                            messages.warning(
                                request,
                                f"This proof of registration expired on {registration_end_date:%d %b %Y}. "
                                f"Please upload your current ({timezone.now().year}) proof of registration."
                            )
                            return render(request, 'accounts/signup.html', {'form': form})
                        if registration_period_is_implausible(registration_end_date):
                            # Almost certainly a misread year (e.g. 2027
                            # instead of 2026), not a real multi-year
                            # registration - reject the same way rather than
                            # trust a date this far out.
                            messages.warning(
                                request,
                                f"The registration end date on this document ({registration_end_date:%d %b %Y}) "
                                f"doesn't look right - please upload a clearer copy of your current proof of registration."
                            )
                            return render(request, 'accounts/signup.html', {'form': form})

                        # The form pre-fills the student number from the
                        # document, but the student can edit it before
                        # submitting - so without this, a classmate's genuine
                        # document plus your own typed number verified you.
                        # A mismatch (or a number the OCR couldn't read at
                        # all) goes to a human rather than being rejected
                        # outright, because a single misread digit would
                        # otherwise lock out a legitimate student.
                        extracted_number = normalise_student_number(ocr_result.get('student_number'))
                        if extracted_number != normalise_student_number(user.student_number):
                            student_number_mismatch = True
                            review_reason = 'number_mismatch'
                            user.is_verified = False
                            if extracted_number:
                                messages.warning(
                                    request,
                                    'The student number on your document does not match the one you entered. '
                                    'Your account has been created and sent to an administrator for manual review.'
                                )
                            else:
                                messages.warning(
                                    request,
                                    'We could not read a student number from your document. '
                                    'Your account has been created and sent to an administrator for manual review.'
                                )
                        elif ocr_result.get('needs_human_review'):
                            # The document's text appeared to address the AI,
                            # so it was never shown to the model - a person
                            # decides instead of the automated check.
                            review_reason = 'suspicious_content'
                            user.is_verified = False
                            messages.warning(
                                request,
                                'Your document could not be verified automatically. '
                                'Your account has been created and sent to a staff member for review.'
                            )
                        else:
                            user.is_verified = True
                            # Only bind the document to the account once it has
                            # actually verified one - a rejected document isn't
                            # "used up", so the student can retry with the same file.
                            user.registration_proof_hash = proof_hash
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
                        if ocr_result and ocr_result.get('confidence_score', 0) >= 0.75:
                            # Close enough that a person should look rather
                            # than the student being told to start over.
                            review_reason = 'low_confidence'
                            messages.warning(
                                request,
                                'Your document could not be verified with enough confidence. '
                                'Your account has been created and sent to a staff member for review.'
                            )
                        else:
                            messages.warning(
                                request,
                                'Your document could not be verified automatically. From your dashboard you can '
                                'upload a clearer copy, or ask a staff member to review your registration.'
                            )
                except Exception:
                    logger.exception("Signup document processing failed")
                    user.is_verified = False
                    messages.warning(
                        request,
                        'Your document could not be processed. From your dashboard you can upload it again, '
                        'or ask a staff member to review your registration.'
                    )
            
            _t_save = timezone.now()
            user.save()
            logger.info(f"user.save() took {(timezone.now() - _t_save).total_seconds():.2f}s")
            if review_reason:
                ReviewRequest.open_for(user, review_reason)
            _t_delete = timezone.now()
            delete_registration_proof(user)
            logger.info(f"delete_registration_proof() took {(timezone.now() - _t_delete).total_seconds():.2f}s")

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
                logger.warning(f"Error tracking signup metrics: {str(e)}")
            logger.info(f"SignupMetrics.create() took {(timezone.now() - _t_signup_metrics).total_seconds():.2f}s")

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
                        # A student-number mismatch always needs a human,
                        # whatever the confidence.
                        manual_correction_required=(75 <= confidence_percent < 80) or student_number_mismatch,
                        student_number_mismatch=student_number_mismatch,
                        document_quality_score=ocr_result.get('quality_score', 0) * 100,
                        document_classification_confidence=ocr_result.get('document_classification_confidence', 0) * 100,
                    )
                except Exception as e:
                    logger.warning(f"Error tracking OCR metrics: {str(e)}")
                logger.info(f"OCRMetrics.create() took {(timezone.now() - _t_ocr_metrics).total_seconds():.2f}s")
            
            # Issue the card if verified. When email confirmation is required,
            # the card is deferred until confirm_email() runs - the document
            # alone never proves the uploader is the person named on it, so
            # control of {student_number}@tut4life.ac.za is used as the second
            # factor before a card exists.
            if user.is_verified:
                _issue_first_card(request, user)

            # Auto-login the user
            _t_login = timezone.now()
            login(request, user)
            logger.info(f"login() took {(timezone.now() - _t_login).total_seconds():.2f}s")

            # Attribute their pre-signup page visits (e.g. viewing the signup
            # form) to the account that just got created.
            _t_link_visits = timezone.now()
            link_recent_anonymous_visits(user, request)
            logger.info(f"link_recent_anonymous_visits() took {(timezone.now() - _t_link_visits).total_seconds():.2f}s")

            try:
                email_send_start = timezone.now()
                send_confirmation_email(user, request)
                email_send_duration = (timezone.now() - email_send_start).total_seconds()
                logger.info(f"Confirmation email handed to {settings.EMAIL_BACKEND} for user {user.pk} (took {email_send_duration:.2f}s)")
                if email_is_delivered():
                    messages.info(request, 'Please check your email to confirm your address.')
                else:
                    messages.warning(request, EMAIL_NOT_CONFIGURED_MESSAGE)
            except Exception as e:
                logger.warning(f"Error sending confirmation email to user {user.pk}: {str(e)}")
                messages.warning(
                    request,
                    "Your account was created, but we couldn't send your confirmation email. "
                    "Use 'Resend' on your dashboard to try again."
                )

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
        exists = bool(email) and User.objects.filter(email__iexact=email, is_active=True).exists()

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
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
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
            # Only these fields: writing the whole row could undo a staff
            # change made since the user was loaded.
            user.save(update_fields=['email_confirmed', 'email_confirmed_at'])
            messages.success(request, 'Your email address has been confirmed!')
            # Card issue is deferred to this point when confirmation is
            # required, so this is where a verified student finally gets one.
            try:
                outcome = issue_card_if_allowed(user, request)
                if outcome == 'issued':
                    messages.success(request, 'Your digital student card has been generated!')
                elif outcome == 'awaiting_photo':
                    messages.info(request, PHOTO_NEEDED_MESSAGE)
            except Exception:
                logger.exception("Card generation failed after email confirmation for user %s", user.pk)
                messages.warning(
                    request,
                    'Your email is confirmed, but your card could not be generated right now. '
                    'Please try again later or contact the student card office.'
                )
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
                    logger.info(f"Confirmation email handed to {settings.EMAIL_BACKEND} for user {request.user.pk}")
                    if email_is_delivered():
                        messages.success(request, 'Confirmation email sent - please check your inbox.')
                    else:
                        messages.warning(request, EMAIL_NOT_CONFIGURED_MESSAGE)
                except Exception as e:
                    logger.warning(f"Error sending confirmation email to user {request.user.pk}: {str(e)}")
                    messages.error(request, 'Could not send the confirmation email right now. Please try again later.')
    elif getattr(request, 'limited', False):
        messages.error(request, 'Too many resend attempts - please wait a while and try again.')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard')


@login_required(login_url='login')
@require_http_methods(['POST'])
@ratelimit(key='user', rate='5/h', method='POST', block=False)
def request_review(request):
    """Lets a student whose registration wasn't verified automatically ask a
    staff member to check it instead (POPIA s71 - the automated decision
    isn't final)."""
    if request.user.is_staff:
        return redirect('performance_dashboard')
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many requests - please wait a while and try again.')
    elif request.user.is_verified:
        messages.info(request, 'Your registration is already verified.')
    else:
        ReviewRequest.open_for(
            request.user, 'student_request', message=request.POST.get('message', '').strip()[:1000],
        )
        messages.success(
            request,
            'Your request has been sent. A staff member will check your registration with the university.'
        )
    return redirect('dashboard')


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

    # A revoked card was switched off deliberately by an administrator, not by
    # time running out - uploading a document must not be a way around that.
    # Checked before the GET render too, so the page doesn't invite an upload
    # that can never succeed.
    existing_card = StudentCard.objects.filter(user=request.user).first()
    if existing_card and existing_card.status == 'revoked':
        messages.error(
            request,
            'Your card has been revoked by an administrator and cannot be reactivated by uploading a document. '
            'Please contact the student card office.'
        )
        return redirect('dashboard')

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
    except Exception:
        logger.exception("Re-upload processing failed for user %s", request.user.pk)
        messages.warning(request, 'Could not process this document. Please try again.')
        return redirect('reupload_proof')

    if isinstance(ocr_result, dict) and ocr_result.get('error') == 'invalid_pdf':
        messages.warning(request, 'This does not look like a valid TUT proof of registration. Please upload the correct document.')
        return redirect('reupload_proof')

    if not ocr_result or ocr_result.get('confidence_score', 0) < 0.8:
        messages.warning(request, 'This document could not be verified with enough confidence. Please upload a clearer copy of your current proof of registration.')
        return redirect('reupload_proof')

    # The account already has a verified student number, so unlike signup
    # there's nothing to review - a document for a different student is simply
    # the wrong document. Without this, a student whose registration lapsed
    # could extend their own card using a classmate's current proof.
    extracted_number = normalise_student_number(ocr_result.get('student_number'))
    if not extracted_number:
        messages.warning(request, 'We could not read a student number from this document. Please upload a clearer copy.')
        return redirect('reupload_proof')
    if extracted_number != normalise_student_number(request.user.student_number):
        messages.warning(request, 'This proof of registration belongs to a different student number. Please upload your own.')
        return redirect('reupload_proof')
    if ocr_result.get('needs_human_review'):
        # The text appeared to address the AI, so a person decides.
        ReviewRequest.open_for(request.user, 'suspicious_content')
        messages.warning(
            request,
            'This document could not be verified automatically, so it has been sent to a staff member for review.'
        )
        return redirect('dashboard')

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

    # OCR takes several seconds, so the card is checked again under a lock:
    # one revoked by staff meanwhile must stay revoked.
    with transaction.atomic():
        student_card = StudentCard.objects.select_for_update().filter(user=request.user).first()
        if student_card and student_card.status == 'revoked':
            messages.error(
                request,
                'Your card has been revoked by an administrator and cannot be reactivated by uploading a document. '
                'Please contact the student card office.'
            )
            return redirect('dashboard')

        user = request.user
        user.registration_end_date = registration_end_date
        user.is_verified = True
        user.save(update_fields=['registration_end_date', 'is_verified'])
        # Their own document now verifies them, so nothing is left for staff to decide.
        ReviewRequest.objects.filter(user=user, status='pending').update(
            status='approved', resolved_at=timezone.now(),
            staff_note='Closed automatically: the student uploaded a document that verified.',
        )

        if student_card:
            student_card.expiry_date = registration_end_date
            student_card.status = 'active'
            student_card.save(update_fields=['expiry_date', 'status'])
            CardActivity.objects.create(student_card=student_card, activity_type='reactivated')
            messages.success(request, 'Your digital student card has been reactivated!')
    if not student_card:
        _issue_first_card(request, user)

    return redirect('dashboard')


def _issue_first_card(request, user):
    """Issue a newly verified student's first card under the one card rule
    (issue_card_if_allowed), and tell them what happened."""
    try:
        outcome = issue_card_if_allowed(user, request)
    except Exception:
        logger.exception("Card generation failed for user %s", user.pk)
        messages.error(request, 'Your card could not be generated right now. Please try again later.')
        return
    if outcome == 'issued':
        messages.success(request, 'Your digital student card has been generated!')
    elif outcome == 'awaiting_email':
        messages.info(request, 'Your registration was verified. Confirm your email address to receive your digital student card.')
    elif outcome == 'awaiting_photo':
        messages.info(request, PHOTO_NEEDED_MESSAGE)


@require_http_methods(['GET', 'POST'])
# Outer ceiling per network. It is set for a campus, where many students
# share one public IP address; failed-login protection is per account and
# network instead (login_guard).
@ratelimit(key='ip', rate='100/m', method='POST', block=False)
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

    if request.method == 'POST':
        posted_email = (request.POST.get('email') or '').strip().lower()
        client_ip = get_client_ip(request)
        wait = login_wait_seconds(posted_email, client_ip)
        if wait:
            messages.error(request, f'Too many failed login attempts. Please wait {wait} seconds and try again.')
            return render(request, 'accounts/login.html', {'form': form})

    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        password = form.cleaned_data['password']

        try:
            # Emails are case-insensitive in practice (e.g. TUT student mail),
            # so match the same way regardless of how it was typed at signup.
            matched_user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            record_failed_login(posted_email, client_ip)
            form.add_error('email', 'No account found with this email address.')
        else:
            authenticated_user = authenticate(request, username=matched_user.username, password=password)
            if authenticated_user is not None:
                clear_failed_logins(posted_email, client_ip)
                login(request, authenticated_user)
                messages.success(request, f'Welcome back, {authenticated_user.first_name}!')
                return post_login_redirect(authenticated_user)
            record_failed_login(posted_email, client_ip)
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


def _requested_name(user):
    """While a name change waits for staff, the profile form shows the
    requested name - otherwise saving any other detail would post the old
    name back and quietly withdraw the request."""
    if user.name_change_requested_at:
        return {'first_name': user.pending_first_name, 'last_name': user.pending_last_name}
    return {}


@login_required(login_url='login')
def profile(request):
    """
    Display and edit user profile, and let the student change their own
    password. Two independent forms share this page, distinguished by
    which submit button was clicked.
    """
    user = request.user

    if request.method == 'POST' and request.POST.get('form_type') == 'password':
        form = CustomUserChangeForm(instance=user, initial=_requested_name(user))
        # Locked like the profile save below: saving the password writes the
        # whole row back.
        with transaction.atomic():
            password_form = StyledPasswordChangeForm(User.objects.select_for_update().get(pk=user.pk), request.POST)
            if password_form.is_valid():
                updated_user = password_form.save()
                # Keep the user logged in - changing the password would
                # otherwise invalidate their current session.
                update_session_auth_hash(request, updated_user)
                messages.success(request, 'Password changed successfully!')
                return redirect('profile')
    elif request.method == 'POST':
        # Locked for the whole save, like the staff approve/reject views:
        # otherwise a staff decision made at the same moment could be undone
        # by this save writing back the row as it was when the page loaded.
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            # Read before binding the form: validating it writes the submitted
            # values onto `user`.
            current_photo = user.profile_picture.name
            current_name = (user.first_name, user.last_name)
            form = CustomUserChangeForm(request.POST, request.FILES, instance=user)
            password_form = StyledPasswordChangeForm(user)
            if form.is_valid():
                new_photo = 'profile_picture' in request.FILES
                new_name = (form.cleaned_data['first_name'], form.cleaned_data['last_name'])
                has_card = StudentCard.objects.filter(user=user).exists()
                updated = form.save(commit=False)
                waiting = []

                # Once the card is issued, its photo and name are what a guard
                # checks the holder against, so changes to them wait for a staff
                # member instead of appearing on the card straight away. Other
                # details (phone and so on) save as normal.
                photo_locked = has_card and settings.REQUIRE_PHOTO_FOR_CARD
                replaced_pending_photo = None
                withdrawn = False
                if photo_locked and new_photo:
                    replaced_pending_photo = updated.pending_profile_picture.name or None
                    updated.pending_profile_picture = form.cleaned_data['profile_picture']
                    # Face comparison only with the student's explicit consent
                    # (biometric data, POPIA s27); otherwise staff compare by eye.
                    updated.pending_photo_compare_consent = (
                        settings.FACE_COMPARISON_ENABLED and request.POST.get('face_compare_consent') == 'on'
                    )
                    waiting.append('photo')
                if photo_locked:
                    # Always the photo the card was issued with - a new upload is
                    # waiting above, and the form's "clear" option must not remove
                    # it either.
                    updated.profile_picture = current_photo
                if has_card and new_name != current_name:
                    if (updated.pending_first_name, updated.pending_last_name) != new_name or not updated.name_change_requested_at:
                        updated.name_change_requested_at = timezone.now()
                    updated.pending_first_name, updated.pending_last_name = new_name
                    updated.first_name, updated.last_name = current_name
                    waiting.append('name')
                elif has_card and updated.name_change_requested_at:
                    # Back to the name on the card: the request is withdrawn, so
                    # staff can't approve a name the student no longer wants.
                    updated.pending_first_name = updated.pending_last_name = ''
                    updated.name_change_requested_at = None
                    withdrawn = True

                updated.save()
                if replaced_pending_photo:
                    # Only once the new one is saved - deleting first would
                    # lose both photos if the save failed.
                    storage = updated.pending_profile_picture.storage
                    transaction.on_commit(lambda: storage.delete(replaced_pending_photo))

                if withdrawn:
                    messages.info(request, 'Your name change request was withdrawn - your card keeps your current name.')
                if waiting:
                    messages.success(
                        request,
                        f"Profile updated. Your new {' and '.join(waiting)} will appear on your card "
                        f"once a staff member approves {'them' if len(waiting) > 1 else 'it'}."
                    )
                elif not withdrawn:
                    messages.success(request, 'Profile updated successfully!')
                if new_photo and not has_card and user.is_verified:
                    # The photo may have been the last thing the card was waiting for.
                    _issue_first_card(request, user)
                return redirect('dashboard')
    else:
        form = CustomUserChangeForm(instance=user, initial=_requested_name(user))
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
        # Once the card exists, a new photo waits for staff - and can, with
        # consent, be compared automatically with the current one.
        'photo_locked': student_card is not None and settings.REQUIRE_PHOTO_FOR_CARD,
        'face_comparison_enabled': settings.FACE_COMPARISON_ENABLED,
    }
    return render(request, 'accounts/profile.html', context)


@staff_member_required(login_url='login')
def user_search(request):
    """Old address of the student search. It showed every student's email,
    student number and card number, and since the email is the student
    number @tut4life.ac.za, students could look each other up - so it is
    staff-only now and forwards to the staff directory."""
    query = request.GET.get('q', '')
    url = reverse('staff_student_list')
    return redirect(f'{url}?{urlencode({"q": query})}' if query else url)


@staff_member_required(login_url='login')
def student_detail(request, student_id):
    """Old address of a student's profile - staff-only, see user_search."""
    return redirect('staff_student_detail', user_id=student_id)
