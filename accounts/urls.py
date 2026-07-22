from django.urls import path
from django.contrib.auth import views as auth_views
from django_ratelimit.decorators import ratelimit
from . import views

# Rate-limited so the password reset form can't be used to spam email sends.
rate_limited_password_reset_view = ratelimit(key='ip', rate='5/h', method='POST', block=True)(
    auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset.html',
        email_template_name='accounts/password_reset_email.html',
        subject_template_name='accounts/password_reset_subject.txt',
    )
)

urlpatterns = [
    path('signup/', views.signup, name='signup'),
    path('signup/preview-proof/', views.preview_registration_proof, name='preview_registration_proof'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('search/', views.user_search, name='user_search'),
    path('student/<int:student_id>/', views.student_detail, name='student_detail'),

    path('confirm-email/<uidb64>/<token>/', views.confirm_email, name='confirm_email'),
    path('resend-confirmation/', views.resend_confirmation_email, name='resend_confirmation'),

    path('password-reset/', rate_limited_password_reset_view, name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html',
    ), name='password_reset_complete'),
]
