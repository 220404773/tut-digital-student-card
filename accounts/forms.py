from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm, PasswordChangeForm
from .models import StudentUser


class CustomUserCreationForm(UserCreationForm):
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
    
    class Meta:
        model = StudentUser
        fields = ('email', 'first_name', 'last_name', 'student_number', 'phone',
                  'registration_proof', 'password1', 'password2')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})
        self.fields['last_name'].widget.attrs.setdefault('placeholder', 'Surname')
    
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
        return registration_proof


class CustomUserChangeForm(UserChangeForm):
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


class StyledPasswordChangeForm(PasswordChangeForm):
    """PasswordChangeForm with the same Bootstrap styling as the rest of the app."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs.update({'class': 'form-control'})


class LoginForm(forms.Form):
    """
    Login form. Field-level errors let the template highlight exactly
    which field (email vs password) caused the failure.
    """
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email', 'autofocus': True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'}))
