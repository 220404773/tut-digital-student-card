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

MODELS = {
    'face_recognition_sface_2021dec.onnx': (
        'https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx',
        '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
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
                data = response.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise CommandError(f'{name}: checksum mismatch (got {actual}) - not saved')
            target.write_bytes(data)
            self.stdout.write(self.style.SUCCESS(f'{name}: downloaded and verified'))
