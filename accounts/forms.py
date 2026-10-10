from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, PasswordChangeForm
from django.core.files.uploadedfile import UploadedFile
from .models import User


class AccessibleFormMixin:
    """Wires each widget to its own help text and error message.

    templates/form_field.html renders those two blocks with predictable ids
    ("<auto_id>_help" / "<auto_id>_error"), but a template can't add attributes
    to an already-rendered widget - so the aria-describedby that points at them,
    and the aria-invalid that marks a field as failed, have to be set here.
    Without this a screen reader reads the error as loose text somewhere after
    the input, with nothing tying the two together.
    """

    def apply_accessibility_attrs(self):
        # Touching self.errors on a bound form triggers validation, so the
        # error state is already known by the time we render.
        errors = self.errors if self.is_bound else {}
        for name, field in self.fields.items():
            described_by = []
            if field.help_text:
                described_by.append(f'{self[name].auto_id}_help')
            if name in errors:
                described_by.append(f'{self[name].auto_id}_error')
                field.widget.attrs['aria-invalid'] = 'true'
            if described_by:
                field.widget.attrs['aria-describedby'] = ' '.join(described_by)


class CustomUserCreationForm(AccessibleFormMixin, UserCreationForm):
    """
    Form for creating a new user during registration.
    """
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=100, required=True, label='Name')
    last_name = forms.CharField(max_length=100, required=True, label='Surname')
    student_number = forms.CharField(max_length=20, required=True)
    phone = forms.CharField(max_length=20, required=False)
    registration_proof = forms.FileField(
        required=True,
        help_text='Upload your proof of registration (PDF, JPG, PNG). Max 5MB.'
    )
    # POPIA puts the burden of proving consent on whoever processes the data,
    # so this isn't only a checkbox to tick - signup() stamps consent_given_at
    # from it, giving a record of when consent was actually given.
    consent = forms.BooleanField(
        required=True,
        label='I have read and accept the Privacy Policy and Terms & Conditions, and I consent to my '
              'personal information and proof of registration being processed as described in them.',
        error_messages={
            'required': 'You need to accept the Privacy Policy and Terms & Conditions before you can create an account.',
        },
    )
    # Only shown after the document check rejects an upload: lets the student
    # create the account anyway and have a staff member decide (POPIA s71),
    # instead of the automated rejection being final.
    request_review = forms.BooleanField(
        required=False,
        label='Ask a staff member to verify my registration',
    )

    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name', 'student_number', 'phone',
                  'registration_proof', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            # Checkboxes take Bootstrap's form-check-input; form-control would
            # stretch them to full width.
            css_class = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'
            field.widget.attrs.update({'class': css_class})
        self.fields['last_name'].widget.attrs.setdefault('placeholder', 'Surname')
        self.apply_accessibility_attrs()
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('Email already registered.')
        return email
    
    def clean_student_number(self):
        student_number = self.cleaned_data.get('student_number')
        if User.objects.filter(student_number=student_number).exists():
            raise forms.ValidationError('Student number already registered.')
        return student_number

    def clean(self):
        cleaned_data = super().clean()
        # Email confirmation is only worth anything as proof of identity if the
        # mailbox belongs to the student number being claimed. Left free-form,
        # someone could pair another student's document and number with their
        # own Gmail, confirm that, and be issued the other student's card.
        email = cleaned_data.get('email')
        student_number = cleaned_data.get('student_number')
        if settings.REQUIRE_EMAIL_CONFIRMATION_FOR_CARD and email and student_number:
            expected = f'{student_number.strip()}@tut4life.ac.za'
            if email.strip().lower() != expected.lower():
                self.add_error('email', f'Use your TUT student email address for this student number ({expected}).')
        return cleaned_data

    # Magic bytes for each allowed type - checked against actual file content,
    # not just the filename extension, which anyone can rename.
    FILE_SIGNATURES = {
        'pdf': (b'%PDF-',),
        'jpg': (b'\xff\xd8\xff',),
        'jpeg': (b'\xff\xd8\xff',),
        'png': (b'\x89PNG\r\n\x1a\n',),
    }

    def clean_registration_proof(self):
        registration_proof = self.cleaned_data.get('registration_proof')
        if registration_proof:
            # Check file size (5MB)
            if registration_proof.size > 5242880:
                raise forms.ValidationError('File size exceeds 5MB limit.')
            # Check file extension
            allowed_extensions = ['pdf', 'jpg', 'jpeg', 'png']
            ext = registration_proof.name.split('.')[-1].lower()
            if ext not in allowed_extensions:
                raise forms.ValidationError('Invalid file format. Allowed: PDF, JPG, PNG')

            # Check the file's actual content matches its claimed type - a
            # renamed executable or script with a .pdf/.png extension would
            # otherwise sail straight through the check above.
            registration_proof.seek(0)
            header = registration_proof.read(8)
            registration_proof.seek(0)
            expected_signatures = self.FILE_SIGNATURES.get(ext, ())
            if not any(header.startswith(sig) for sig in expected_signatures):
                raise forms.ValidationError('This file does not look like a valid PDF, JPG, or PNG. Please upload the original file.')
        return registration_proof


class CustomUserChangeForm(AccessibleFormMixin, UserChangeForm):
    """
    Form for updating user profile information. Email is deliberately not
    part of this form - it's shown read-only in the template (disabled
    inputs aren't submitted by the browser), so including it here would let
    every save silently blank it out.
    """
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'phone', 'date_of_birth', 'gender', 'profile_picture')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.apply_accessibility_attrs()

    # Same signature check used on registration_proof - ImageField's default
    # validation only confirms Pillow can decode the file as *some* image, it
    # doesn't restrict which format, and it's still validating file content
    # against an unrestricted extension. This is the field every other
    # student's browser renders (dashboard, staff pages, public verify page),
    # so it gets the same content-matches-extension check as the document
    # upload, not just Pillow's implicit one.
    PICTURE_SIGNATURES = {
        'jpg': (b'\xff\xd8\xff',),
        'jpeg': (b'\xff\xd8\xff',),
        'png': (b'\x89PNG\r\n\x1a\n',),
    }

    def clean_profile_picture(self):
        picture = self.cleaned_data.get('profile_picture')
        # A plain edit that doesn't touch the photo returns the existing,
        # already-stored FieldFile here, not a fresh upload - only a real
        # UploadedFile (this request's multipart data) needs re-validating.
        if not picture or not isinstance(picture, UploadedFile):
            return picture

        if picture.size > 5242880:
            raise forms.ValidationError('Image exceeds 5MB limit.')

        ext = picture.name.rsplit('.', 1)[-1].lower() if '.' in picture.name else ''
        if ext not in self.PICTURE_SIGNATURES:
            raise forms.ValidationError('Invalid image format. Allowed: JPG, PNG.')

        picture.seek(0)
        header = picture.read(8)
        picture.seek(0)
        if not any(header.startswith(sig) for sig in self.PICTURE_SIGNATURES[ext]):
            raise forms.ValidationError('This file does not look like a valid JPG or PNG. Please upload the original file.')

        return picture


class StyledPasswordChangeForm(AccessibleFormMixin, PasswordChangeForm):
    """PasswordChangeForm with the same Bootstrap styling as the rest of the app."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.apply_accessibility_attrs()


class LoginForm(AccessibleFormMixin, forms.Form):
    """
    Login form. Field-level errors let the template highlight exactly
    which field (email vs password) caused the failure.
    """
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email', 'autofocus': True, 'autocomplete': 'email'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password', 'autocomplete': 'current-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_accessibility_attrs()
