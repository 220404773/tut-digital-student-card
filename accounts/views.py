from urllib.parse import urlencode
from django.conf import settings
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
from cards.models import StudentCard, SignupMetrics, OCRMetrics
from cards.utils import process_registration_proof, generate_student_card, parse_registration_end_date, link_recent_anonymous_visits


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
        ocr_result = process_registration_proof(registration_proof)
        if isinstance(ocr_result, dict) and ocr_result.get('error') == 'invalid_pdf':
            return JsonResponse({'success': False, 'message': 'This does not look like a valid TUT proof of registration. Please upload the correct document.'}, status=400)

        if not ocr_result:
            return JsonResponse({'success': False, 'message': 'No extractable information was found in the uploaded document.'}, status=400)

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
        signup_start_time = timezone.now()
        form = CustomUserCreationForm(request.POST, request.FILES)
        
        if form.is_valid():
            user = form.save(commit=False)
            # AbstractUser requires a unique username; the form only collects an
            # email, so derive the username from it instead of leaving it blank
            # (which caused every second signup to collide on the unique constraint).
            user.username = user.email

            # Track file upload metrics
            file_upload_size_mb = 0
            ocr_start_time = None
            ocr_duration = 0
            
            # Process the uploaded registration proof with OCR
            if 'registration_proof' in request.FILES:
                registration_proof = request.FILES['registration_proof']
                user.registration_proof = registration_proof
                file_upload_size_mb = registration_proof.size / (1024 * 1024)  # Convert to MB
                
                # Run OCR verification
                try:
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
                        user.is_verified = True
                        user.ocr_student_number = ocr_result.get('student_number')
                        user.ocr_full_name = ocr_result.get('full_name')
                        if not user.student_number and ocr_result.get('student_number'):
                            user.student_number = ocr_result.get('student_number')
                        if ocr_result.get('full_name'):
                            first_name, last_name = split_full_name(ocr_result.get('full_name'))
                            if not user.first_name and first_name:
                                user.first_name = first_name
                            if not user.last_name and last_name:
                                user.last_name = last_name
                        user.registration_end_date = parse_registration_end_date(ocr_result.get('period'))

                        messages.success(request, 'TUT Proof of Registration verified successfully!')
                    else:
                        user.is_verified = False
                        messages.warning(request, 'OCR verification partially matched. Admin review required.')
                except Exception as e:
                    user.is_verified = False
                    messages.warning(request, f'OCR processing error: {str(e)}. Please try again.')
            
            user.save()
            delete_registration_proof(user)
            
            # Track signup metrics
            signup_end_time = timezone.now()
            signup_duration = (signup_end_time - signup_start_time).total_seconds()
            
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
            
            # Track OCR metrics if OCR was performed
            if ocr_result:
                try:
                    OCRMetrics.objects.create(
                        user=user,
                        ocr_start_time=ocr_start_time,
                        ocr_end_time=ocr_end_time,
                        ocr_duration_seconds=ocr_duration,
                        ocr_confidence_score=ocr_result.get('confidence_score', 0) * 100,
                        extracted_student_number=ocr_result.get('student_number'),
                        extracted_full_name=ocr_result.get('full_name'),
                        is_accurate=user.is_verified,
                        document_quality_score=ocr_result.get('quality_score', 0) * 100,
                        document_classification_confidence=ocr_result.get('document_classification_confidence', 0) * 100,
                    )
                except Exception as e:
                    print(f"Error tracking OCR metrics: {str(e)}")
            
            # Generate digital card if verified
            if user.is_verified:
                try:
                    generate_student_card(user, request)
                    messages.success(request, 'Your digital student card has been generated!')
                except Exception as e:
                    messages.error(request, f'Error generating card: {str(e)}')
            
            # Auto-login the user
            login(request, user)

            # Attribute their pre-signup page visits (e.g. viewing the signup
            # form) to the account that just got created.
            link_recent_anonymous_visits(user, request)

            try:
                send_confirmation_email(user, request)
                messages.info(request, 'Please check your email to confirm your address.')
            except Exception as e:
                print(f"Error sending confirmation email: {str(e)}")

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


def confirm_email(request, uidb64, token):
    """Landing page for the link sent by send_confirmation_email."""
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = StudentUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, StudentUser.DoesNotExist):
        user = None

    if user is not None and email_confirmation_token.check_token(user, token):
        user.email_confirmed = True
        user.save()
        messages.success(request, 'Your email address has been confirmed!')
    else:
        messages.error(request, 'This confirmation link is invalid or has expired. Request a new one from your dashboard.')

    if request.user.is_authenticated:
        return post_login_redirect(request.user)
    return redirect('login')


@login_required(login_url='login')
@ratelimit(key='user', rate='5/h', method='POST', block=False)
def resend_confirmation_email(request):
    """Lets a student request a fresh confirmation link if the first one
    expired or never arrived."""
    if request.method == 'POST' and not getattr(request, 'limited', False):
        if not request.user.email_confirmed:
            try:
                send_confirmation_email(request.user, request)
                messages.success(request, 'Confirmation email sent - please check your inbox.')
            except Exception as e:
                messages.error(request, 'Could not send the confirmation email right now. Please try again later.')
    elif getattr(request, 'limited', False):
        messages.error(request, 'Too many resend attempts - please wait a while and try again.')
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard')


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


@login_required(login_url='login')
def logout_view(request):
    """
    Handle user logout.
    """
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('home')


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
