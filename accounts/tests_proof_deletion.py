"""Data minimisation (POPIA): the uploaded proof of registration is read once
and then deleted - only the fields read from it are kept. This runs a real PDF
through the real reader (no AI provider is called) and then looks at the disk.
"""
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import fitz
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.tests_identity import signup_payload

MEDIA = tempfile.mkdtemp()


def proof_pdf(student_number='220404773', name='KEKELETSO KHAKA'):
    end = timezone.now().date() + timedelta(days=60)
    start = end - timedelta(days=300)
    text = (
        "TSHWANE UNIVERSITY OF TECHNOLOGY\nTO WHOM IT MAY CONCERN\n"
        f"It is hereby confirmed that {name}, Student Number {student_number}\n"
        "ID Number 9607015050081\nQualification: ADRS20 AdvDip (Computer Science)\n"
        f"Registered From {start:%d-%b-%Y} to {end:%d-%b-%Y}\nCampus and Registration Type: Full Time\nREGISTRAR\n"
    )
    document = fitz.open()
    document.new_page().insert_textbox(fitz.Rect(50, 60, 545, 780), text, fontsize=11)
    data = document.tobytes()
    document.close()
    return data


@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=False, REQUIRE_EMAIL_CONFIRMATION_FOR_CARD=True)
class ProofIsDeletedAfterReadingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()

    def _files_on_disk(self):
        return [path.name for path in Path(MEDIA).rglob('*') if path.is_file()]

    def test_no_copy_of_the_document_is_left_after_a_verified_signup(self):
        upload = SimpleUploadedFile('proof.pdf', proof_pdf(), content_type='application/pdf')
        with patch('cards.utils.get_llm_clients', return_value=[]):  # rule-based reading only
            response = self.client.post(reverse('signup'), signup_payload(registration_proof=upload))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

        user = User.objects.get(student_number='220404773')
        self.assertTrue(user.is_verified)                  # it was read and accepted ...
        self.assertFalse(user.registration_proof)          # ... no file is referenced ...
        self.assertEqual(self._files_on_disk(), [])        # ... and none is left on disk, not even a temporary copy
        self.assertEqual(len(user.registration_proof_hash), 64)  # only a fingerprint remains, to stop re-use

    def test_no_copy_is_left_when_the_document_is_rejected_either(self):
        upload = SimpleUploadedFile('timetable.pdf', b'%PDF-1.4 not a proof of registration', content_type='application/pdf')
        with patch('cards.utils.get_llm_clients', return_value=[]):
            self.client.post(reverse('signup'), signup_payload(registration_proof=upload))
        self.assertEqual(self._files_on_disk(), [])
