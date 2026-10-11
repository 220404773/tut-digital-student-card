"""Downloads the face-comparison model (39 MB, too large to commit).

    python manage.py download_face_models

Checked against a fixed SHA-256, so a changed or corrupted download is
refused rather than used. The small face-detection model is committed.
"""
import hashlib
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.face_checks import COMPARISON_MODEL, MODEL_SHA256

MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024

# The checksum lives in face_checks.py, which also checks it before loading.
MODELS = {
    COMPARISON_MODEL: (
        'https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx',
        MODEL_SHA256[COMPARISON_MODEL],
    ),
}


class Command(BaseCommand):
    help = 'Download the face-comparison model and verify its checksum.'

    def handle(self, *args, **options):
        folder = Path(settings.FACE_MODELS_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        for name, (url, expected) in MODELS.items():
            target = folder / name
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
                self.stdout.write(f'{name}: already present')
                continue
            with urllib.request.urlopen(url, timeout=120) as response:
                # The model is 39 MB: anything past 60 MB is not the right file.
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise CommandError(f'{name}: download larger than expected - not saved')
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise CommandError(f'{name}: checksum mismatch (got {actual}) - not saved')
            # Written to a temporary name and then renamed, so an interrupted
            # write never leaves a half-written model under the real name.
            partial = target.with_suffix('.partial')
            partial.write_bytes(data)
            partial.replace(target)
            self.stdout.write(self.style.SUCCESS(f'{name}: downloaded and verified'))
