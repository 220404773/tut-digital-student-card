from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, PasswordChangeForm
from .models import StudentUser


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

    class Meta:
        model = StudentUser
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
        if StudentUser.objects.filter(email=email).exists():
            raise forms.ValidationError('Email already registered.')
        return email
    
    def clean_student_number(self):
        student_number = self.cleaned_data.get('student_number')
        if StudentUser.objects.filter(student_number=student_number).exists():
            raise forms.ValidationError('Student number already registered.')
        return student_number
    
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
        model = StudentUser
        fields = ('first_name', 'last_name', 'phone', 'date_of_birth', 'gender', 'profile_picture')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.apply_accessibility_attrs()


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
