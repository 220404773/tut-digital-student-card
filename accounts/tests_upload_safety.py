"""Uploaded content must never be executable, and the format it claims to be
must match what it actually is - profile_picture gets the same content-vs-
extension check registration_proof already had, since it's the one upload
served back to *other* users' browsers rather than deleted after processing.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from accounts.forms import CustomUserChangeForm
from accounts.models import User


def real_image_bytes(fmt):
    """A genuinely decodable 1x1 image - Django's ImageField runs Pillow's
    own decode check in addition to this form's signature check, so a fake
    fixture that only satisfies the magic-byte prefix isn't enough."""
    buf = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buf, format=fmt)
    return buf.getvalue()


REAL_PNG = real_image_bytes('PNG')
REAL_JPG = real_image_bytes('JPEG')


# File-type safety, not faces: the test images are 1x1 pixels - see tests_face_checks.py.
@override_settings(PHOTO_FACE_CHECK_ENABLED=False)
class ProfilePictureUploadSafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='pic@tut4life.ac.za', email='pic@tut4life.ac.za',
            student_number='220404780', password='StrongPass123!',
        )

    def _form(self, picture):
        return CustomUserChangeForm(
            data={'first_name': 'Pic', 'last_name': 'Test', 'phone': '', 'gender': ''},
            files={'profile_picture': picture},
            instance=self.user,
        )

    def test_genuine_png_is_accepted(self):
        form = self._form(SimpleUploadedFile('photo.png', REAL_PNG, content_type='image/png'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_genuine_jpg_is_accepted(self):
        form = self._form(SimpleUploadedFile('photo.jpg', REAL_JPG, content_type='image/jpeg'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_html_renamed_to_png_is_rejected(self):
        """The exact attack this closes: content that isn't an image at all,
        wearing an image extension, aimed at other users' browsers."""
        payload = b'<script>alert(document.cookie)</script>'
        form = self._form(SimpleUploadedFile('photo.png', payload, content_type='image/png'))
        self.assertFalse(form.is_valid())
        self.assertIn('profile_picture', form.errors)

    def test_disallowed_extension_is_rejected_even_with_real_image_bytes(self):
        form = self._form(SimpleUploadedFile('photo.svg', REAL_PNG, content_type='image/svg+xml'))
        self.assertFalse(form.is_valid())
        self.assertIn('profile_picture', form.errors)

    def test_oversized_image_is_rejected(self):
        oversized = REAL_PNG + b'\x00' * (6 * 1024 * 1024)
        form = self._form(SimpleUploadedFile('photo.png', oversized, content_type='image/png'))
        self.assertFalse(form.is_valid())
        self.assertIn('profile_picture', form.errors)

    def test_editing_profile_without_touching_the_photo_does_not_reject_the_existing_one(self):
        """The existing stored FieldFile isn't a fresh upload and must not be
        re-run through the new-upload validation path."""
        form = CustomUserChangeForm(
            data={'first_name': 'Changed', 'last_name': 'Test', 'phone': '', 'gender': ''},
            files={},
            instance=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
