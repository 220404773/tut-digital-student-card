"""evaluate_ocr scores classification exactly as signup acts on it, and never
credits a document the pipeline couldn't read."""
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.utils import timezone

END = timezone.now().date() + timedelta(days=60)
GENUINE = {
    'student_number': '220404773', 'full_name': 'Kekeletso Khaka',
    'period': f'01-Jan-{END.year} to {END:%d-%b-%Y}', 'confidence_score': 0.95,
    'document_classification_reason': 'Matches a TUT proof of registration.',
}
UNREADABLE = {
    'student_number': None, 'full_name': None, 'confidence_score': 0.5,
    'document_classification_reason': 'Document uploaded successfully, but no readable text could be extracted.',
}

# What the (mocked) pipeline returns for each file.
PIPELINE = {
    'proof_ok.pdf': GENUINE,
    'proof_wrong_number.pdf': {**GENUINE, 'student_number': '220404999'},
    'proof_unreadable.png': UNREADABLE,
    'timetable.png': {'error': 'invalid_pdf'},
    'fee_statement.pdf': GENUINE,  # wrongly accepted
}


class EvaluateOcrTests(SimpleTestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        for name in PIPELINE:
            (self.folder / name).write_bytes(b'placeholder')
        (self.folder / 'labels.csv').write_text(
            'filename,is_proof,student_number,full_name,period_end\n'
            f'proof_ok.pdf,yes,220404773,Kekeletso Khaka,{END.isoformat()}\n'
            f'proof_wrong_number.pdf,yes,220404773,Kekeletso Khaka,{END.isoformat()}\n'
            f'proof_unreadable.png,yes,220404773,Kekeletso Khaka,{END.isoformat()}\n'
            'timetable.png,no,,,\n'
            'fee_statement.pdf,no,,,\n',
            encoding='utf-8',
        )

    def _run(self):
        out = StringIO()
        with patch('cards.management.commands.evaluate_ocr.process_registration_proof',
                   side_effect=lambda f: PIPELINE[f.name]):
            call_command('evaluate_ocr', str(self.folder), stdout=out)
        return out.getvalue()

    def test_reports_classification_accuracy_against_the_target(self):
        output = self._run()
        # Correct: proof_ok, proof_wrong_number (still a proof), timetable.
        # Wrong: proof_unreadable (no decision made), fee_statement (accepted).
        self.assertIn('ET-01 classification accuracy: 3/5 = 60% (target 90%) - NOT MET', output)

    def test_field_accuracy_is_reported_separately_from_classification(self):
        output = self._run()
        self.assertIn('number, name and end date all correct: 1/3', output)
        self.assertIn('would auto-verify (confidence >= 0.8): 2/3', output)

    def test_personal_values_are_never_printed(self):
        output = self._run()
        self.assertNotIn('220404773', output)
        self.assertNotIn('Khaka', output)

    def test_missing_document_is_an_error_not_a_silent_skip(self):
        (self.folder / 'timetable.png').unlink()
        with self.assertRaises(CommandError):
            self._run()
