from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('card/', views.view_card, name='view_card'),
    path('card/download/', views.download_qr_code, name='download_qr'),
    path('verify/<str:card_number>/', views.verify_card_public, name='verify_card_public'),
    path('performance/', views.performance_dashboard, name='performance_dashboard'),
    path('reports/cards/', views.report_cards, name='report_cards'),
    path('reports/signups/', views.report_signups, name='report_signups'),
    path('reports/email-confirmation/', views.report_email_confirmation, name='report_email_confirmation'),
    path('reports/ocr/', views.report_ocr, name='report_ocr'),
    path('reports/card-activity/', views.report_card_activity, name='report_card_activity'),
    path('reports/deleted-students/', views.report_deleted_students, name='report_deleted_students'),
    path('reports/deleted-students/<int:log_id>/reissue/', views.reissue_deleted_student, name='reissue_deleted_student'),
    path('reports/visits/', views.report_visits, name='report_visits'),
    path('reports/visitors/', views.report_visit_users, name='report_visit_users'),
    path('reports/visitors/<int:user_id>/', views.report_user_visits, name='report_user_visits'),
    path('students/', views.staff_student_list, name='staff_student_list'),
    path('students/<int:user_id>/', views.staff_student_detail, name='staff_student_detail'),
]
