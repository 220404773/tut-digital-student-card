from datetime import datetime, timedelta

from django.contrib import admin
from .models import StudentCard
from .utils import generate_unique_card_number


@admin.register(StudentCard)
class StudentCardAdmin(admin.ModelAdmin):
    list_display = ('card_number', 'user', 'status', 'issue_date', 'expiry_date')
    list_filter = ('status', 'issue_date')
    search_fields = ('card_number', 'user__email', 'user__student_number')
    # card_number is auto-generated the same way issue_date is (auto_now_add) -
    # not meant to be typed in by hand. The QR code is drawn from it on demand.
    readonly_fields = ('card_number', 'issue_date')
    actions = ['populate_missing_card_details']

    @admin.action(description='Populate missing card number')
    def populate_missing_card_details(self, request, queryset):
        fixed = 0
        for card in queryset:
            if not card.card_number:
                card.card_number = generate_unique_card_number(card.user)
                card.save()
                fixed += 1
        self.message_user(request, f'Populated a card number for {fixed} card(s).')

    def save_model(self, request, obj, form, change):
        if not obj.card_number:
            obj.card_number = generate_unique_card_number(obj.user)
        if not obj.expiry_date:
            obj.expiry_date = obj.user.registration_end_date or (datetime.now().date() + timedelta(days=365))

        super().save_model(request, obj, form, change)
