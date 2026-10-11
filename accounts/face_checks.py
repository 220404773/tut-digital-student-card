"""Face checks on card photos, run locally with OpenCV's own models (see
accounts/face_models/README.md) - photos never leave the server.

photo_problems(): face *detection* on every upload. It only finds where a
face is, it doesn't identify anyone, and it rejects photos that couldn't
serve as a card photo: no face, several faces, a face too small or too blurry.

compare_faces(): face *recognition* - biometric processing, which POPIA treats
as special personal information. It runs only when FACE_COMPARISON_ENABLED is
on and the student consented, only advises the staff member reviewing a photo
change (it never approves or rejects), and its result is not stored.

Both fail open: if a model is missing or OpenCV errors, nothing is blocked -
the problem is logged and a staff member still looks at every photo change.
"""
import io
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

DETECTION_MODEL = 'face_detection_yunet_2023mar.onnx'
COMPARISON_MODEL = 'face_recognition_sface_2021dec.onnx'
# SHA-256 of each model, checked before it is first loaded in a process - a
# truncated download or a swapped file is refused rather than run.
MODEL_SHA256 = {
    DETECTION_MODEL: '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    COMPARISON_MODEL: '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79',
}
# A photo's header is read before the image is decoded: a small file can
# declare an enormous image, and decoding that would exhaust the server's
# memory (the 5 MB upload limit counts bytes, not pixels). 25 MP is well above
# any phone camera.
MAX_PIXELS = 25_000_000

# The face must span at least this share of the photo's shorter side - a
# card photo is a head-and-shoulders shot, not a distant figure.
MIN_FACE_SHARE = 0.2
# Sharpness of the face (variance of the Laplacian on a 112x112 crop).
# Deliberately low, so only clearly blurred photos are refused.
MIN_SHARPNESS = 10.0
# SFace cosine similarity: OpenCV's published threshold for "same person" is
# 0.363. Below LIKELY_DIFFERENT is treated as clearly different; the band in
# between is reported as unclear rather than forced either way.
SAME_PERSON_THRESHOLD = 0.363
LIKELY_DIFFERENT_BELOW = 0.25
MAX_SIDE = 1024


def _model_path(name):
    return Path(settings.FACE_MODELS_DIR) / name


_verified_models = {}


def _model_ok(name):
    """True if the model file is present and matches its known checksum.
    Checked once per process per file version, not on every upload."""
    import hashlib

    path = _model_path(name)
    try:
        version = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    except OSError:
        return False
    if version not in _verified_models:
        _verified_models[version] = hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256[name]
        if not _verified_models[version]:
            logger.warning("Face model %s does not match its checksum - not used", name)
    return _verified_models[version]


class ImageTooLarge(Exception):
    """The image declares more pixels than MAX_PIXELS."""


def _decode(fileobj):
    """An OpenCV image from an uploaded or stored file, downscaled so a large
    phone photo stays quick to check. None if it isn't a readable image."""
    import cv2
    import numpy as np
    from PIL import Image

    fileobj.seek(0)
    data = fileobj.read()
    fileobj.seek(0)
    try:
        # Opening reads only the header, so this costs nothing even for a
        # file that declares a huge image.
        width, height = Image.open(io.BytesIO(data)).size
    except Exception:
        return None
    if width * height > MAX_PIXELS:
        raise ImageTooLarge()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    scale = MAX_SIDE / max(height, width)
    if scale < 1:
        image = cv2.resize(image, (int(width * scale), int(height * scale)))
    return image


def _find_faces(image):
    """Detected faces, largest first. Each row: x, y, w, h, 5 landmarks, score."""
    import cv2

    height, width = image.shape[:2]
    detector = cv2.FaceDetectorYN.create(str(_model_path(DETECTION_MODEL)), '', (width, height), 0.8, 0.3, 50)
    _, faces = detector.detect(image)
    if faces is None:
        return []
    return sorted(faces, key=lambda face: face[2] * face[3], reverse=True)


def photo_problems(fileobj):
    """Reasons this upload can't be a card photo, in words for the student.
    An empty list means it's fine (or the check couldn't run)."""
    if not _model_ok(DETECTION_MODEL):
        logger.warning("Face detection model missing or altered - photo checks skipped")
        return []
    try:
        import cv2

        try:
            image = _decode(fileobj)
        except ImageTooLarge:
            return ['This photo is too large. Please use a normal phone photo (under 25 megapixels).']
        if image is None:
            return []  # not an image at all: the form's own checks say so
        faces = _find_faces(image)
        if not faces:
            return ["We couldn't find a face in this photo. Use a clear, front-facing photo of just you."]
        if len(faces) > 1:
            return ['This photo shows more than one person. Use a photo of just you.']

        x, y, w, h = (int(v) for v in faces[0][:4])
        if w < MIN_FACE_SHARE * min(image.shape[:2]):
            return ['Your face is too small in this photo. Take it closer, from the shoulders up.']
        crop = image[max(y, 0):y + h, max(x, 0):x + w]
        if crop.size:
            gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_64F).var() < MIN_SHARPNESS:
                return ['This photo is too blurry. Hold the camera still in good light and try again.']
        return []
    except Exception:
        logger.exception("Face detection failed - photo checks skipped")
        return []


def compare_faces(current_file, new_file):
    """How alike the faces in two photos are, as advice for staff:
    {'score': 0.62, 'level': 'same' | 'unclear' | 'different', 'verdict': '...'}.
    None when comparison is off, the model is missing, or a face isn't found.
    Nothing is stored: the face measurements exist only for this call."""
    if not settings.FACE_COMPARISON_ENABLED:
        return None
    if not (_model_ok(DETECTION_MODEL) and _model_ok(COMPARISON_MODEL)):
        logger.warning("Face comparison model missing or altered - run manage.py download_face_models")
        return None
    try:
        import cv2

        recognizer = cv2.FaceRecognizerSF.create(str(_model_path(COMPARISON_MODEL)), '')
        features = []
        for fileobj in (current_file, new_file):
            try:
                image = _decode(fileobj)
            except ImageTooLarge:
                return None
            faces = _find_faces(image) if image is not None else []
            if not faces:
                return None
            features.append(recognizer.feature(recognizer.alignCrop(image, faces[0])))
        score = float(recognizer.match(features[0], features[1], cv2.FaceRecognizerSF_FR_COSINE))
    except Exception:
        logger.exception("Face comparison failed")
        return None

    if score >= SAME_PERSON_THRESHOLD:
        level, verdict = 'same', 'Likely the same person'
    elif score < LIKELY_DIFFERENT_BELOW:
        level, verdict = 'different', 'Likely a different person'
    else:
        level, verdict = 'unclear', 'Unclear - compare carefully'
    return {'score': round(score, 2), 'level': level, 'verdict': verdict}
