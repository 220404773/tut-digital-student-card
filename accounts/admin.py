import logging
import re
from collections import Counter

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse, reverse_lazy
from django.utils import timezone
from cards.models import StudentCard, CardActivity
from cards.utils import generate_unique_card_number, issue_card_if_allowed
from .models import User, DeletedStudentLog, ReviewRequest
from .notices import CLOSED_NOTICE, REISSUED_NOTICE, REVOKED_NOTICE, email_student, notify_student
from .services import delete_registration_proof

logger = logging.getLogger(__name__)

STAFF_ADMIN_NUMBER_RE = re.compile(r'^STAFF-ADMIN-(\d+)$')


class StudentUserAdmin(UserAdmin):
    model = User
    list_display = ('email', 'first_name', 'last_name', 'student_number', 'is_verified', 'is_staff')
    # Overrides UserAdmin.fieldsets rather than extending it, dropping
    # groups/user_permissions - admin accounts here are just is_staff +
    # is_superuser, no per-permission management needed.
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('Student Info', {'fields': ('student_number', 'phone', 'date_of_birth', 'gender', 'profile_picture')}),
        ('Registration', {'fields': ('registration_proof', 'is_verified', 'registration_end_date')}),
    )
    actions = ['revoke_card_action', 'reissue_card_action']
    # "View on site" normally links to the object's own get_absolute_url()
    # (User has none) - point it at the admin reporting dashboard
    # instead, since that's the natural "site" a staff member editing a
    # student wants to jump to from here.
    view_on_site = lambda self, obj: reverse('performance_dashboard')

    def _revoke_single_card(self, student, request):
        """Returns True if the student had a card to revoke."""
        card = StudentCard.objects.filter(user=student).first()
        if not card:
            return False
        card.status = 'revoked'
        card.save()
        CardActivity.objects.create(student_card=card, activity_type='revoked', performed_by=request.user)
        notify_student(student, REVOKED_NOTICE, level='danger', request=request)
        return True

    def _reissue_single_card(self, student, request):
        """Returns 'reissued', 'issued', 'awaiting_email', 'awaiting_photo', 'not_verified' or 'failed'."""
        card = StudentCard.objects.filter(user=student).first()
        if not card:
            # No card yet - issue one under the same rule as every other path
            # (verified, and email confirmed), rather than silently doing nothing.
            try:
                outcome = issue_card_if_allowed(student, request)
            except Exception:
                logger.exception("Card generation failed for student %s", student.pk)
                return 'failed'
            if outcome == 'issued':
                notify_student(student, 'Your digital student card has been issued.', level='success', request=request)
            return outcome

        # A new card number is enough: the QR code is drawn from it each time
        # it's shown, so the old QR code stops working with no file to clean up.
        card.card_number = generate_unique_card_number(student)
        card.status = 'active'
        # Restore the expiry from the student's actual proof of registration,
        # not whatever was already on the card - otherwise reissuing an
        # expired card would leave expiry_date in the past and it would
        # immediately flip back to 'expired' on the next refresh_status().
        card.expiry_date = student.registration_end_date or card.expiry_date
        card.save()
        CardActivity.objects.create(student_card=card, activity_type='reissued', performed_by=request.user)
        notify_student(student, REISSUED_NOTICE, level='warning', request=request)
        return 'reissued'

    @admin.action(description='Revoke digital card')
    def revoke_card_action(self, request, queryset):
        revoked = sum(1 for student in queryset if self._revoke_single_card(student, request))
        skipped = queryset.count() - revoked

        message = f'Revoked {revoked} card(s).'
        if skipped:
            message += f' {skipped} selected student(s) had no card to revoke.'
        self.message_user(request, message)

    @admin.action(description='Reissue digital card (new card number + QR code)')
    def reissue_card_action(self, request, queryset):
        outcomes = Counter(self._reissue_single_card(student, request) for student in queryset)
        message = f"Reissued {outcomes['reissued']} card(s), newly issued {outcomes['issued']} card(s)."
        if outcomes['awaiting_email']:
            message += f" {outcomes['awaiting_email']} student(s) will get their card once they confirm their email."
        if outcomes['awaiting_photo']:
            message += f" {outcomes['awaiting_photo']} student(s) will get their card once they add a profile photo."
        if outcomes['not_verified']:
            message += f" {outcomes['not_verified']} student(s) aren't verified, so no card was issued."
        if outcomes['failed']:
            message += f" Failed to issue a card for {outcomes['failed']} student(s)."
        self.message_user(request, message)

    def get_urls(self):
        custom_urls = [
            path(
                '<int:student_id>/revoke-card/',
                self.admin_site.admin_view(self.revoke_card_view),
                name='accounts_user_revoke_card',
            ),
            path(
                '<int:student_id>/reissue-card/',
                self.admin_site.admin_view(self.reissue_card_view),
                name='accounts_user_reissue_card',
            ),
        ]
        return custom_urls + super().get_urls()

    def revoke_card_view(self, request, student_id):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        student = get_object_or_404(User, pk=student_id)
        if self._revoke_single_card(student, request):
            self.message_user(request, f"Revoked {student.get_full_name_display()}'s digital card.")
        else:
            self.message_user(request, f'{student.get_full_name_display()} has no card to revoke.', level=messages.WARNING)
        return redirect('admin:accounts_user_change', student.pk)

    def reissue_card_view(self, request, student_id):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])
        student = get_object_or_404(User, pk=student_id)
        outcome = self._reissue_single_card(student, request)
        name = student.get_full_name_display()
        if outcome == 'reissued':
            self.message_user(request, f"Reissued {name}'s digital card (new card number and QR code).")
        elif outcome == 'issued':
            self.message_user(request, f'{name} had no card yet - issued one.')
        elif outcome == 'awaiting_email':
            self.message_user(request, f'{name} will get their card once they confirm their email.', level=messages.WARNING)
        elif outcome == 'awaiting_photo':
            self.message_user(request, f'{name} will get their card once they add a profile photo.', level=messages.WARNING)
        elif outcome == 'not_verified':
            self.message_user(request, f"{name} isn't verified, so no card was issued.", level=messages.WARNING)
        else:
            self.message_user(request, f"Couldn't issue a card for {name}.", level=messages.ERROR)
        return redirect('admin:accounts_user_change', student.pk)

    # Details a student sees on their card or uses to sign in: a staff member
    # changing them tells the student.
    NOTIFIED_FIELDS = ('first_name', 'last_name', 'email', 'student_number', 'phone', 'date_of_birth',
                       'gender', 'profile_picture', 'registration_end_date')

    def _notify_changes(self, obj, form, request):
        if form is None:  # saved without a form: nothing to compare
            return
        changed = form.changed_data
        if {'first_name', 'last_name', 'student_number'} & set(changed):
            from cards.signals import sync_wallet_for_student
            sync_wallet_for_student(obj)  # shown on the Wallet pass
        details = [str(form.fields[name].label).lower() for name in self.NOTIFIED_FIELDS if name in changed]
        if details:
            notify_student(obj, 'The student card office updated your ' + ', '.join(details) + '.',
                           level='info', request=request)
        if 'is_verified' in changed and not obj.is_verified:
            notify_student(obj, 'The student card office withdrew the verification of your registration. '
                                'Contact them if you think this is a mistake.', level='warning', request=request)
        if 'is_active' in changed:
            if obj.is_active:
                notify_student(obj, 'Your account has been re-activated by the student card office.',
                               level='success', request=request)
            else:
                notify_student(obj, 'Your account has been deactivated by the student card office, so you cannot '
                                    'sign in. Contact them if you think this is a mistake.',
                               level='danger', request=request)

    def _log_deletion(self, obj, deleted_by):
        # Deleting a User cascades - their StudentCard, CardActivity,
        # and metrics rows all disappear with it. Capture a snapshot before
        # that happens, or there'd be no record the student ever existed.
        card = StudentCard.objects.filter(user=obj).first()
        DeletedStudentLog.objects.create(
            student_number=obj.student_number,
            full_name=obj.get_full_name_display(),
            email=obj.email,
            was_verified=obj.is_verified,
            had_card=card is not None,
            card_number=card.card_number if card else '',
            registration_end_date=obj.registration_end_date,
            was_email_confirmed=obj.email_confirmed,
            deleted_by=deleted_by,
        )

    def _email_closed_account(self, obj, request):
        # The account (and so any dashboard notice) is gone - email only.
        if not obj.is_staff:
            transaction.on_commit(lambda: email_student(obj, CLOSED_NOTICE, request))

    def delete_model(self, request, obj):
        self._log_deletion(obj, request.user)
        self._email_closed_account(obj, request)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._log_deletion(obj, request.user)
            self._email_closed_account(obj, request)
        super().delete_queryset(request, queryset)

    def save_model(self, request, obj, form, change):
        # Staff+superuser accounts aren't real students - give them a
        # placeholder student_number instead of requiring a real one.
        # student_number is unique, so each admin gets its own numbered
        # placeholder (STAFF-ADMIN-1, -2, ...) rather than colliding on a
        # single shared value. Only assigns a new one if not already using
        # this placeholder scheme, so it doesn't reassign on every save.
        if obj.is_staff and obj.is_superuser and not STAFF_ADMIN_NUMBER_RE.match(obj.student_number or ''):
            used_numbers = set()
            for number in User.objects.filter(
                student_number__startswith='STAFF-ADMIN-'
            ).exclude(pk=obj.pk).values_list('student_number', flat=True):
                match = STAFF_ADMIN_NUMBER_RE.match(number)
                if match:
                    used_numbers.add(int(match.group(1)))
            next_number = 1
            while next_number in used_numbers:
                next_number += 1
            obj.student_number = f'STAFF-ADMIN-{next_number}'

        super().save_model(request, obj, form, change)
        if change and not obj.is_staff:
            self._notify_changes(obj, form, request)

        # The "Registration" fieldset exposes registration_proof directly -
        # if a staff member uploads one here, there's no OCR step to trigger
        # the usual cleanup, so it would otherwise sit in storage
        # indefinitely. This field is never meant to persist either way.
        if obj.registration_proof:
            delete_registration_proof(obj)

        # Ticking "is_verified" here is a manual approval - equivalent to the
        # OCR auto-verification path in signup(), so the card follows the same
        # rule: issued now, or once the student confirms their email.
        if obj.is_verified and not obj.is_staff:
            try:
                # Closing the review and issuing the card succeed or fail
                # together - the same all-or-nothing rule as a staff approval
                # on the Review Requests page.
                with transaction.atomic():
                    # A staff member has just decided, so nothing is left in
                    # the review queue for this student.
                    closed = ReviewRequest.objects.filter(user=obj, status='pending').update(
                        status='approved', resolved_at=timezone.now(), resolved_by=request.user,
                        staff_note='Closed: verified by a staff member in the admin.',
                    )
                    outcome = issue_card_if_allowed(obj, request)
            except Exception:
                logger.exception("Card generation failed while verifying student %s in the admin", obj.pk)
                # Don't leave them verified without a card and with their
                # review silently closed: undo the verification too.
                User.objects.filter(pk=obj.pk).update(is_verified=False)
                obj.is_verified = False
                self.message_user(
                    request,
                    'The card could not be generated, so the student was left unverified and any review '
                    'request stays open. Please try again.',
                    level=messages.ERROR,
                )
                return
            if closed:
                self.message_user(request, 'Their pending review request was closed.')
            if form is not None and 'is_verified' in form.changed_data:
                next_step = {
                    'issued': ' Your digital student card is ready on your Home page.',
                    'awaiting_email': ' Confirm your email address to receive your digital student card.',
                    'awaiting_photo': ' Add a profile photo on your Profile page to receive your digital student card.',
                }.get(outcome, '')
                notify_student(obj, 'Your registration has been verified by the student card office.' + next_step,
                               level='success', request=request)
            if outcome == 'issued':
                self.message_user(request, 'Digital student card generated for this student.')
            elif outcome == 'awaiting_email':
                self.message_user(request, 'Student verified - their card will be issued once they confirm their email.')
            elif outcome == 'awaiting_photo':
                self.message_user(request, 'Student verified - their card will be issued once they add a profile photo.')


admin.site.register(User, StudentUserAdmin)


@admin.register(ReviewRequest)
class ReviewRequestAdmin(admin.ModelAdmin):
    """View-only. Decisions are made on the Review Requests report, which also
    verifies the student, sets their registration end date and issues the
    card - editing a request's status here would mark it decided while none
    of that happened."""
    list_display = ('user', 'reason', 'status', 'created_at', 'resolved_by', 'resolved_at')
    list_filter = ('status', 'reason')
    search_fields = ('user__student_number', 'user__email')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # The decided requests are the record that a person made the
        # decision (POPIA s71), so they can't be deleted on their own. But
        # deleting a student takes their requests with them - refusing that
        # made every student with a decision on record undeletable.
        url_name = getattr(getattr(request, 'resolver_match', None), 'url_name', '')
        return url_name in ('accounts_user_delete', 'accounts_user_changelist') and request.user.has_perm(
            'accounts.delete_user')
admin.site.unregister(Group)

# The "View site" link in the admin header (admin/base.html's {{ site_url }})
# defaults to "/" - staff using this admin have no reason to land on the
# public homepage, so send it straight to the reporting dashboard instead.
admin.site.site_url = reverse_lazy('performance_dashboard')
