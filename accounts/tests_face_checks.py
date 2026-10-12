"""Face checks on card photos (accounts/face_checks.py).

Detection uses the real model where it can be shown without a photo of a real
person: a photo with no face is refused. The other cases fake the detector's
output, and the comparison fakes the recogniser, because these tests don't
use photos of real people.
"""
import io
import shutil
import tempfile
from unittest.mock import patch

import numpy as np
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts import face_checks
from accounts.models import User
from cards.models import StudentCard

MEDIA = tempfile.mkdtemp()


def image_file(size=400, noise=False, name='me.png'):
    if noise:
        pixels = np.random.default_rng(1).integers(0, 255, (size, size, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
    else:
        image = Image.new('RGB', (size, size), 'lightgrey')
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def fake_face(x, y, w, h):
    return np.array([x, y, w, h] + [0] * 10 + [0.99], dtype=np.float32)


class PhotoProblemsTests(SimpleTestCase):
    def test_a_photo_with_no_face_is_refused(self):
        """Real detection model, on a plain image."""
        self.assertIn("couldn't find a face", face_checks.photo_problems(image_file())[0])

    def test_more_than_one_person_is_refused(self):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(10, 10, 150, 150), fake_face(200, 200, 150, 150)]):
            self.assertIn('more than one person', face_checks.photo_problems(image_file(noise=True))[0])

    def test_a_face_too_small_is_refused(self):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(10, 10, 40, 40)]):
            self.assertIn('too small', face_checks.photo_problems(image_file(noise=True))[0])

    def test_a_blurry_face_is_refused(self):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]):
            self.assertIn('too blurry', face_checks.photo_problems(image_file())[0])

    def test_one_clear_face_is_accepted(self):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]):
            self.assertEqual(face_checks.photo_problems(image_file(noise=True)), [])

    def test_a_missing_model_does_not_block_uploads(self):
        """Fails open: staff still look at every photo change."""
        with override_settings(FACE_MODELS_DIR=tempfile.gettempdir() + '/no-models-here'):
            self.assertEqual(face_checks.photo_problems(image_file()), [])

    def test_an_altered_model_is_not_used(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with open(f'{folder}/{face_checks.DETECTION_MODEL}', 'wb') as f:
            f.write(b'not the real model')
        with override_settings(FACE_MODELS_DIR=folder),                 patch.object(face_checks, '_find_faces') as find_faces:
            self.assertEqual(face_checks.photo_problems(image_file()), [])
        find_faces.assert_not_called()

    def test_a_photo_declaring_too_many_pixels_is_refused_before_decoding(self):
        """A small file can declare a huge image; decoding it would use
        gigabytes of memory, so the header is checked first."""
        buf = io.BytesIO()
        Image.new('L', (6000, 6000)).save(buf, format='PNG')  # 36 MP, a few KB
        upload = SimpleUploadedFile('big.png', buf.getvalue(), content_type='image/png')
        with patch('cv2.imdecode') as imdecode:
            self.assertIn('too large', face_checks.photo_problems(upload)[0])
        imdecode.assert_not_called()


class FakeRecognizer:
    def __init__(self, score):
        self.score = score

    def alignCrop(self, image, face):
        return image

    def feature(self, aligned):
        return aligned

    def match(self, a, b, method):
        return self.score


class CompareFacesTests(SimpleTestCase):
    def setUp(self):
        # The comparison model is downloaded at build time, not committed, so
        # these tests mustn't depend on it being present (the recogniser is
        # faked anyway).
        model_ok = patch.object(face_checks, '_model_ok', return_value=True)
        model_ok.start()
        self.addCleanup(model_ok.stop)

    def _compare(self, score):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]), \
                patch('cv2.FaceRecognizerSF.create', return_value=FakeRecognizer(score)):
            return face_checks.compare_faces(image_file(noise=True), image_file(noise=True))

    @override_settings(FACE_COMPARISON_ENABLED=False)
    def test_off_by_default_until_the_privacy_policy_covers_it(self):
        self.assertIsNone(self._compare(0.9))

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_a_high_score_is_likely_the_same_person(self):
        self.assertEqual(self._compare(0.62), {'score': 0.62, 'level': 'same', 'verdict': 'Likely the same person'})

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_a_low_score_is_likely_a_different_person(self):
        self.assertEqual(self._compare(0.1)['level'], 'different')

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_a_borderline_score_is_reported_as_unclear(self):
        self.assertEqual(self._compare(0.3)['level'], 'unclear')

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_no_face_found_gives_no_advice(self):
        with patch.object(face_checks, '_find_faces', return_value=[]) as find_faces,                 patch('cv2.FaceRecognizerSF.create', return_value=FakeRecognizer(0.9)):
            self.assertIsNone(face_checks.compare_faces(image_file(), image_file()))
        find_faces.assert_called()

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_each_face_is_read_as_it_is_and_mirrored(self):
        recognizer = FakeRecognizer(0.7)
        readings = []
        recognizer.feature = lambda aligned: readings.append(aligned) or aligned
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]),                 patch('cv2.FaceRecognizerSF.create', return_value=recognizer):
            face_checks.compare_faces(image_file(noise=True), image_file(noise=True))
        self.assertEqual(len(readings), 4)  # two photos, each read twice
        self.assertTrue((readings[1] == readings[0][:, ::-1]).all())  # the second reading is the mirror image

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_a_missing_comparison_model_gives_no_advice(self):
        with patch.object(face_checks, '_model_ok', side_effect=lambda name: name != face_checks.COMPARISON_MODEL):
            self.assertIsNone(self._compare(0.9))


# Automatic approval pinned off: these tests are about the advice staff see,
# whatever a local .env switches on for a demo.
@override_settings(MEDIA_ROOT=MEDIA, REQUIRE_PHOTO_FOR_CARD=True, FACE_AUTO_APPROVE_ENABLED=False)
class FaceChecksInTheAppTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='220404773@tut4life.ac.za', email='220404773@tut4life.ac.za',
            student_number='220404773', password='StrongPass123!', first_name='Kekeletso', last_name='Khaka',
            is_verified=True, email_confirmed=True,
        )
        self.client.force_login(self.student)

    def _upload(self, **extra):
        data = {'first_name': 'Kekeletso', 'last_name': 'Khaka', 'phone': '', 'gender': '',
                'profile_picture': image_file(noise=True)}
        data.update(extra)
        return self.client.post(reverse('profile'), data, follow=True)

    def _with_card_and_photo(self):
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]):
            self._upload()
        self.assertTrue(StudentCard.objects.filter(user=self.student).exists())

    def test_the_profile_page_explains_what_a_card_photo_must_look_like(self):
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'a recent photo of <strong>just you</strong>', html=False)
        self.assertContains(response, 'no sunglasses, cap, mask or filters')

    def test_a_photo_without_a_face_is_refused_with_the_reason_shown(self):
        response = self._upload(profile_picture=image_file())
        self.assertContains(response, "couldn&#x27;t find a face")
        self.assertFalse(StudentCard.objects.exists())

    @override_settings(FACE_COMPARISON_ENABLED=False)
    def test_no_consent_box_while_comparison_is_off(self):
        self._with_card_and_photo()
        self.assertNotContains(self.client.get(reverse('profile')), 'face_compare_consent')

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_consent_is_recorded_only_when_given(self):
        self._with_card_and_photo()
        self.assertContains(self.client.get(reverse('profile')), 'face_compare_consent')
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]):
            self._upload(face_compare_consent='on')
        self.student.refresh_from_db()
        self.assertTrue(self.student.pending_photo_compare_consent)

    @override_settings(FACE_COMPARISON_ENABLED=True)
    def test_staff_see_the_advice_only_for_students_who_consented(self):
        self._with_card_and_photo()
        with patch.object(face_checks, '_find_faces', return_value=[fake_face(50, 50, 200, 200)]):
            self._upload()  # no consent
        staff = User.objects.create_superuser(
            username='admin@tut4life.ac.za', email='admin@tut4life.ac.za',
            student_number='STAFF-0001', password='StrongPass123!',
        )
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse('report_review_requests')), "Student didn't consent")

        User.objects.filter(pk=self.student.pk).update(pending_photo_compare_consent=True)
        with patch('cards.views.compare_faces', return_value={'score': 0.71, 'level': 'same',
                                                              'verdict': 'Likely the same person'}):
            response = self.client.get(reverse('report_review_requests'))
        self.assertContains(response, 'Likely the same person')
        self.assertContains(response, 'advice only, you decide')

        self.client.post(reverse('resolve_photo_change', args=[self.student.pk]), {'action': 'approve'})
        self.student.refresh_from_db()
        self.assertFalse(self.student.pending_photo_compare_consent)
