"""Measures ET-01 - document classification accuracy - over a hand-labelled
set of documents, using the same pipeline signup uses.

    python manage.py evaluate_ocr path/to/evaluation-set

The folder holds the documents plus a labels.csv written by hand:

    filename,is_proof,student_number,full_name,period_end
    proof_01.pdf,yes,220404773,Kekeletso Khaka,2026-12-31
    timetable.png,no,,,

A genuine proof counts as correctly classified if the pipeline doesn't
reject it; an invalid document, if it does. A document the pipeline couldn't
read any text from is counted as wrong either way - no classification was
made, so it can't be credited as a correct one.

The documents and labels are real students' personal information: keep the
folder outside the repository and delete it once the evaluation is recorded.
Output shows only whether each field matched, never the values themselves.
"""
import csv
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from accounts.views import _normalise_student_number
from cards.utils import parse_registration_end_date, process_registration_proof

ET01_TARGET = 0.90
UNREADABLE_REASON = 'no readable text'


def _outcome(result):
    """Reduce a pipeline result to what signup would do with it."""
    if isinstance(result, dict) and result.get('error') == 'invalid_pdf':
        return 'rejected'
    if not result or UNREADABLE_REASON in (result.get('document_classification_reason') or ''):
        return 'unreadable'
    return 'accepted'


def _same_name(a, b):
    return ' '.join((a or '').lower().split()) == ' '.join((b or '').lower().split())


class Command(BaseCommand):
    help = 'Measure document classification accuracy (ET-01) over a labelled document set.'

    def add_arguments(self, parser):
        parser.add_argument('folder', help='Folder containing the documents and labels.csv')

    def handle(self, *args, **options):
        folder = Path(options['folder'])
        labels_path = folder / 'labels.csv'
        if not labels_path.exists():
            raise CommandError(f'{labels_path} not found - see this command\'s docstring for the format.')

        with open(labels_path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise CommandError('labels.csv has no rows.')

        self.stdout.write(f'Evaluating {len(rows)} document(s)\n')
        self.stdout.write(f'{"file":<28}{"label":<9}{"outcome":<12}{"class":<7}{"number":<8}{"name":<6}{"end date":<9}')

        correct = 0
        genuine = fields_all_match = auto_verified = 0
        for row in rows:
            path = folder / row['filename']
            if not path.exists():
                raise CommandError(f'{row["filename"]} is listed in labels.csv but not in the folder.')
            is_proof = row['is_proof'].strip().lower() in ('yes', 'y', 'true', '1')

            with open(path, 'rb') as handle:
                result = process_registration_proof(File(handle, name=path.name))
            outcome = _outcome(result)

            classified_correctly = (outcome == 'accepted') if is_proof else (outcome == 'rejected')
            correct += classified_correctly

            number_ok = name_ok = date_ok = '-'
            if is_proof:
                genuine += 1
                if outcome == 'accepted':
                    number_ok = _normalise_student_number(result.get('student_number')) == \
                        _normalise_student_number(row['student_number'])
                    name_ok = _same_name(result.get('full_name'), row['full_name'])
                    expected_end = date.fromisoformat(row['period_end']) if row['period_end'].strip() else None
                    date_ok = parse_registration_end_date(result.get('period')) == expected_end
                    fields_all_match += number_ok and name_ok and date_ok
                    auto_verified += result.get('confidence_score', 0) >= settings.OCR_CONFIDENCE_THRESHOLD

            mark = lambda v: v if v == '-' else ('yes' if v else 'NO')
            self.stdout.write(
                f'{row["filename"][:27]:<28}{"proof" if is_proof else "invalid":<9}{outcome:<12}'
                f'{mark(classified_correctly):<7}{mark(number_ok):<8}{mark(name_ok):<6}{mark(date_ok):<9}'
            )

        accuracy = correct / len(rows)
        verdict = self.style.SUCCESS('MET') if accuracy >= ET01_TARGET else self.style.ERROR('NOT MET')
        self.stdout.write('')
        self.stdout.write(f'ET-01 classification accuracy: {correct}/{len(rows)} = {accuracy:.0%} '
                          f'(target {ET01_TARGET:.0%}) - {verdict}')
        if genuine:
            self.stdout.write(f'Genuine documents with number, name and end date all correct: {fields_all_match}/{genuine}')
            self.stdout.write(f'Genuine documents that would auto-verify (confidence >= '
                              f'{settings.OCR_CONFIDENCE_THRESHOLD}): {auto_verified}/{genuine}')
