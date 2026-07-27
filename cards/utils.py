import os
import re
import json
import time
import uuid
import hashlib
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageEnhance
import qrcode
import io

# Try to import pytesseract for OCR
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import fitz
except ImportError:
    fitz = None

_TESSERACT_CONFIGURED = False


def configure_tesseract():
    """Point pytesseract at the Tesseract binary if it isn't already on PATH."""
    global _TESSERACT_CONFIGURED
    if not OCR_AVAILABLE or _TESSERACT_CONFIGURED:
        return

    _TESSERACT_CONFIGURED = True
    tesseract_cmd = getattr(settings, 'TESSERACT_CMD', '') or os.getenv('TESSERACT_CMD', '')
    candidates = [tesseract_cmd] if tesseract_cmd else []
    candidates += [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return

try:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    LLM_AVAILABLE = True
except ImportError:
    ChatNVIDIA = None
    LLM_AVAILABLE = False


def get_nvidia_api_key():
    """Return the NVIDIA API key from Django settings or the environment."""
    return getattr(settings, 'NVIDIA_API_KEY', '') or os.getenv('NVIDIA_API_KEY', '')


def get_llm_client():
    """Create the NVIDIA chat model only when the dependency and key are available."""
    api_key = get_nvidia_api_key()
    if not LLM_AVAILABLE or not api_key:
        return None

    return ChatNVIDIA(
        # Traded down from meta/llama-3.3-70b-instruct for faster inference on
        # the low-CPU deployment instance - some accuracy tradeoff on messier/
        # noisier OCR text is expected in exchange.
        model='meta/llama-3.1-8b-instruct',
        nvidia_api_key=api_key,
    )


def classify_tut_proof_of_registration(text):
    """Classify whether a document is a TUT proof of registration using
    keyword matching. Used as a fallback when the LLM classification path
    (see resolve_document_classification) is unavailable.
    """
    normalized_text = ' '.join((text or '').split()).lower()

    if not normalized_text:
        return {
            'document_type': 'unknown',
            'is_tut_proof_of_registration': False,
            'classification_confidence': 0.0,
            'classification_reason': 'No text extracted from the document.',
        }

    indicators = [
        'tshwane university of technology',
        'to whom it may concern',
        'it is hereby confirmed that',
        'student number',
        'id number',
        'registered from',
        'qualification',
        'campus and registration type',
        'registrar',
    ]

    score = sum(1 for indicator in indicators if indicator in normalized_text)
    confidence = round(score / len(indicators), 2)

    # A document only needs to score highly overall - it no longer has to
    # contain an exact set of phrases verbatim, which was too brittle against
    # OCR noise (e.g. a misread "registered from" failed the whole document
    # even with every other indicator present).
    is_tut_proof = confidence >= 0.8

    return {
        'document_type': 'TUT Proof of Registration' if is_tut_proof else 'unknown',
        'is_tut_proof_of_registration': is_tut_proof,
        'classification_confidence': confidence,
        'classification_reason': (
            'Document contains the expected TUT proof-of-registration wording.'
            if is_tut_proof
            else 'Document does not match the TUT proof-of-registration pattern closely enough.'
        ),
    }


def deskew(image_array):
    """Straighten a scanned page using its dominant text angle. Expects a numpy array."""
    gray = cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]

    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return Image.fromarray(image_array)

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # minAreaRect's angle is unreliable when text only covers part of the page
    # (e.g. a short letter on an otherwise blank page) - it can report ~90 degrees
    # and flip the page sideways. Real scan/photo skew is a few degrees at most,
    # so treat anything larger as a bad reading and leave the image untouched.
    if abs(angle) > 15:
        return Image.fromarray(image_array)

    (h, w) = image_array.shape[:2]
    center = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image_array, rotation_matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rotated)


def binarize(image):
    """Apply Otsu thresholding to a PIL image to increase OCR text contrast."""
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    return Image.fromarray(thresh)


def upscale_and_enhance(pil_image, heavy=False):
    """Upscale small images and boost contrast/sharpness before OCR.

    Phone photos of a document are often smaller than a proper scan and
    taken in inconsistent lighting - upscaling the long edge to a minimum
    size and nudging contrast/sharpness noticeably improves Tesseract's
    word confidence on that kind of source without needing cv2.

    `heavy=True` upscales further (1600px/3x vs the default 1200px/1.8x).
    Upscaling more directly costs more Tesseract runtime (it scales with
    pixel count), so the light target is tried first for every image and
    this is only used as a retry for scans that came back low-confidence.
    """
    image = pil_image.convert('RGB')
    max_dim = max(image.size)
    target, cap = (1600, 3.0) if heavy else (1200, 1.8)
    if max_dim < target:
        scale = min(cap, target / max_dim)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
    image = ImageEnhance.Contrast(image).enhance(1.3)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    return image


def preprocess_for_ocr(pil_image, heavy=False):
    """Upscale/enhance, then deskew and binarize a page image to improve Tesseract accuracy."""
    image = upscale_and_enhance(pil_image, heavy=heavy)

    if not CV2_AVAILABLE:
        return image

    try:
        image_array = np.array(image)
        deskewed = deskew(image_array)
        return binarize(deskewed)
    except Exception as e:
        print(f"Image preprocessing error: {str(e)}")
        return image


def _run_tesseract(image, psm):
    """Run Tesseract once with the given page segmentation mode and return
    (extracted_text, average_word_confidence 0-100, word_count).
    """
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=f'--psm {psm}')

    total_confidence = 0
    word_count = 0
    lines = {}
    for i, word in enumerate(data['text']):
        conf_value = data['conf'][i]
        try:
            conf = int(float(conf_value))
        except (TypeError, ValueError):
            conf = -1
        if conf > -1:
            total_confidence += conf
            word_count += 1
        if word.strip():
            line_key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            lines.setdefault(line_key, []).append(word)

    text = '\n'.join(' '.join(words) for words in lines.values())
    avg_confidence = (total_confidence / word_count) if word_count else 0.0
    return text, avg_confidence, word_count


def _ocr_pass(pil_image, heavy):
    """Preprocess at the given intensity and run Tesseract, retrying with
    --psm 11 (sparse text) if --psm 6 (uniform block) came back low-confidence
    or empty - covers a phone photo taken at an angle or with inconsistent
    spacing. Returns (text, confidence, word_count).
    """
    processed = preprocess_for_ocr(pil_image, heavy=heavy)
    text, confidence, word_count = _run_tesseract(processed, psm=6)

    if word_count == 0 or confidence < 50:
        retry_text, retry_confidence, retry_word_count = _run_tesseract(processed, psm=11)
        if retry_word_count > 0 and retry_confidence > confidence:
            text, confidence, word_count = retry_text, retry_confidence, retry_word_count

    return text, confidence, word_count


def ocr_image_with_confidence(pil_image):
    """Run Tesseract on an image and return (extracted_text, average_word_confidence 0-100).

    Tries the lighter/faster preprocessing target first, since most uploads
    are a clean-enough phone photo that don't need more - only escalates to
    heavier upscaling (more Tesseract runtime, since cost scales with pixel
    count) when that first pass came back weak, the same trade the psm 6/11
    retry inside _ocr_pass already makes. So a good scan still costs one
    Tesseract call, and only a genuinely weak one pays for the extra passes.
    """
    configure_tesseract()

    text, confidence, word_count = _ocr_pass(pil_image, heavy=False)

    if word_count == 0 or confidence < 70:
        heavy_text, heavy_confidence, heavy_word_count = _ocr_pass(pil_image, heavy=True)
        if heavy_word_count > 0 and heavy_confidence > confidence:
            text, confidence, word_count = heavy_text, heavy_confidence, heavy_word_count

    return text, round(confidence, 2)


def hash_uploaded_file(file):
    """Content hash of an uploaded file, used to tell whether the file
    submitted with the signup form is the same one already OCR'd during the
    live preview - so signup() can reuse that result instead of paying for a
    second full OCR+LLM pass on an unchanged file. Resets the file position
    afterwards so callers can still read it from the start.
    """
    file.seek(0)
    digest = hashlib.sha256()
    for chunk in file.chunks():
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def process_registration_proof(file):
    """
    Process uploaded registration proof with OCR.
    Extract student number, name, academic year, and registration status.
    
    Returns:
        dict: OCR results with extracted fields and confidence score
    """
    try:
        # Save file temporarily
        temp_path = os.path.join(settings.MEDIA_ROOT, f'temp_{uuid.uuid4()}.{file.name.split(".")[-1]}')
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        
        with open(temp_path, 'wb') as temp_file:
            for chunk in file.chunks():
                temp_file.write(chunk)
        
        # Extract text from file
        extraction_start = time.perf_counter()
        extracted_text = ""
        ocr_engine_confidence = 0.0
        if file.name.lower().endswith('.pdf'):
            extracted_text = extract_pdf_text(temp_path)
            if not extracted_text.strip() and OCR_AVAILABLE:
                try:
                    images = render_pdf_pages(temp_path)
                    page_confidences = []
                    for image in images:
                        page_text, page_confidence = ocr_image_with_confidence(image)
                        extracted_text += page_text
                        page_confidences.append(page_confidence)
                    if page_confidences:
                        ocr_engine_confidence = sum(page_confidences) / len(page_confidences)
                except Exception as e:
                    print(f"PDF processing error: {str(e)}")
                    # Allow PDF to proceed even if OCR fails - file validation passed
                    extracted_text = "PDF processed (OCR unavailable)"
        else:  # JPG, PNG
            if not OCR_AVAILABLE:
                return None
            image = Image.open(temp_path)
            extracted_text, ocr_engine_confidence = ocr_image_with_confidence(image)
        extraction_duration = time.perf_counter() - extraction_start

        # No usable text to work with (either nothing was extracted, or PDF
        # rendering/OCR failed and left just the sentinel marker) - let the
        # file through so the user can fill in details manually.
        if not extracted_text.strip() or extracted_text.startswith("PDF processed"):
            print(f"OCR pipeline timing: extraction={extraction_duration:.2f}s llm=skipped (no text)")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return {
                'student_number': None,
                'full_name': None,
                'confidence_score': 0.5,
                'document_type': 'Document (text extraction unavailable)',
                'document_classification_confidence': 0.5,
                'document_classification_reason': 'Document uploaded successfully, but no readable text could be extracted. Please enter your details manually.'
            }

        # One LLM call handles both classification and field extraction, so
        # there's no separate round-trip just to classify the document.
        llm_start = time.perf_counter()
        llm_result = extract_with_llm(extracted_text)
        llm_duration = time.perf_counter() - llm_start
        print(f"OCR pipeline timing: extraction={extraction_duration:.2f}s llm={llm_duration:.2f}s total={extraction_duration + llm_duration:.2f}s")
        classification = resolve_document_classification(extracted_text, llm_result)

        if not classification['is_tut_proof_of_registration']:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return {'error': 'invalid_pdf'}

        if llm_result:
            result = llm_result
        else:
            # Extract fields using regex patterns when the LLM is unavailable or fails
            result = extract_fields_from_text(extracted_text)

        result['confidence_score'] = calculate_confidence_score(result, llm_result is not None, ocr_engine_confidence)
        result['ocr_engine_confidence'] = ocr_engine_confidence
        result['quality_score'] = ocr_engine_confidence / 100
        result['full_name'] = result.get('full_name') or result.get('student_name')
        result['student_name'] = result.get('student_name') or result.get('full_name')
        if result.get('student_number'):
            result['email'] = f"{result['student_number']}@tut4life.ac.za"
        result['document_type'] = classification['document_type']
        result['document_classification_confidence'] = classification['classification_confidence']
        result['document_classification_reason'] = classification['classification_reason']

        if not result.get('student_number') and not result.get('full_name'):
            return None
        
        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return result
    
    except Exception as e:
        print(f"OCR processing error: {str(e)}")
        return None


def extract_fields_from_text(text):
    """
    Extract student information from OCR text using regex patterns.
    """
    result = {
        'student_number': None,
        'full_name': None,
        'student_name': None,
        'email': None,
        'academic_year': None,
        'id_number': None,
        'university_name': None,
        'qualification': None,
        'period': None,
        'registration_status': None,
    }
    
    normalized_text = ' '.join(text.split())

    # Pattern for student number in the confirmation sentence
    student_number_match = re.search(
        r'confirmed\s+that\s+.+?,\s*Student\s*Number\s*(\d{6,10})\b',
        normalized_text,
        re.IGNORECASE,
    )
    if not student_number_match:
        student_number_match = re.search(r'Student\s*Number\s*[:#\-]?\s*(\d{6,10})', normalized_text, re.IGNORECASE)
    if not student_number_match:
        student_number_match = re.search(r'\b\d{6,10}\b', normalized_text)
    if student_number_match:
        result['student_number'] = student_number_match.group(1) if student_number_match.lastindex else student_number_match.group()

    # Pattern for student email if present on the document
    email_match = re.search(r'\b\d{6,10}@tut4life\.ac\.za\b', normalized_text, re.IGNORECASE)
    if email_match:
        result['email'] = email_match.group()

    # Pattern for full name from the exact TUT registration wording
    name_match = re.search(
        r'confirmed\s+that\s+([A-Z][A-Z\s\-\']+?),\s*Student\s*Number\s*\d{6,10}\b',
        normalized_text,
        re.IGNORECASE,
    )
    if name_match:
        result['full_name'] = ' '.join(name_match.group(1).split())
        result['student_name'] = result['full_name']
    else:
        # Fallback for scanned PDFs where the confirmation sentence wraps across lines
        line_match = re.search(
            r'It\s+is\s+hereby\s+confirmed\s+that\s+([A-Z][A-Z\s\-\']+?),\s*Student\s*Number',
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        if line_match:
            result['full_name'] = ' '.join(line_match.group(1).split())
            result['student_name'] = result['full_name']

    # Pattern for id number
    id_number_match = re.search(r'ID\s*Number\s*[:#\-]?\s*(\d{10,13})', normalized_text, re.IGNORECASE)
    if id_number_match:
        result['id_number'] = id_number_match.group(1)

    # Pattern for university name
    if re.search(r'Tshwane\s+University\s+of\s+Technology', normalized_text, re.IGNORECASE):
        result['university_name'] = 'Tshwane University of Technology'

    # Pattern for qualification
    qualification_match = re.search(r'Qualification\s*:\s*([^\n\r]+)', text, re.IGNORECASE)
    if qualification_match:
        result['qualification'] = qualification_match.group(1).strip()

    # Pattern for period / registration window
    period_match = re.search(r'registered\s+from\s+([^\n]+?\d{4})\s+to\s+([^\n]+?\d{4})', text, re.IGNORECASE)
    if period_match:
        result['period'] = f"{period_match.group(1).strip()} to {period_match.group(2).strip()}"
    
    # Pattern for academic year
    year_match = re.search(r'\b(202[0-9]|201[0-9])\b', text)
    if year_match:
        result['academic_year'] = year_match.group()
    
    # Pattern for registration status
    if re.search(r'active|registered|confirmed', text, re.IGNORECASE):
        result['registration_status'] = 'Active'
    else:
        result['registration_status'] = 'Pending'
    
    return result


def extract_with_llm(raw_text):
    """Use an LLM to both classify the document and structure its fields in a
    single call, when NVIDIA access is available. Combining the two avoids a
    second round-trip just for classification.
    """
    llm = get_llm_client()
    if llm is None:
        return None

    prompt = (
        "You are an expert at analyzing TUT (Tshwane University of Technology) proof-of-registration documents. "
        "First decide whether the provided text is genuinely a TUT proof of registration. "
        "Then extract the requested fields. Use only the provided text - do not guess. If a field is missing, return null.\n\n"
        "Return valid JSON only with these keys:\n"
        "is_tut_proof_of_registration (true or false), "
        "classification_confidence (a number from 0.0 to 1.0, how confident you are in that classification), "
        "classification_reason (a short explanation), "
        "student_number, student_name, full_name, id_number, academic_year, university_name, qualification, period, email.\n\n"
        f"Text:\n{raw_text}"
    )

    try:
        response = llm.invoke(prompt, response_format={"type": "json_object"})
        data = json.loads(response.content)
        return normalize_llm_output(data)
    except Exception as e:
        print(f"LLM output parsing error: {str(e)}")
        return None


def normalize_llm_output(data):
    """Normalize LLM output keys to the structure used by the project."""
    result = {
        'student_number': data.get('student_number') or data.get('Student Number'),
        'student_name': data.get('student_name') or data.get('Student Name') or data.get('full_name'),
        'full_name': data.get('full_name') or data.get('student_name') or data.get('Student Name'),
        'id_number': data.get('id_number') or data.get('ID Number'),
        'academic_year': data.get('academic_year') or data.get('Academic Year'),
        'university_name': data.get('university_name') or data.get('University Name'),
        'qualification': data.get('qualification') or data.get('Qualification'),
        'period': data.get('period') or data.get('Period'),
        'email': data.get('email') or data.get('Email'),
        'registration_status': data.get('registration_status') or data.get('Registration Status'),
        'is_tut_proof_of_registration': data.get('is_tut_proof_of_registration'),
        'classification_confidence': data.get('classification_confidence'),
        'classification_reason': data.get('classification_reason'),
    }

    if result.get('student_name'):
        result['student_name'] = str(result['student_name']).strip()
    if result.get('full_name'):
        result['full_name'] = str(result['full_name']).strip()

    return result


def resolve_document_classification(extracted_text, llm_result):
    """Decide whether the document is a TUT proof of registration.

    Prefers the classification the LLM already returned as part of its
    combined extraction call (no second request needed). Falls back to the
    rule-based keyword classifier when the LLM is unavailable, failed, or
    didn't return a usable classification confidence.
    """
    if llm_result is not None:
        confidence = llm_result.get('classification_confidence')
        if isinstance(confidence, (int, float)):
            # Tolerate a model that answers in 0-100 instead of 0.0-1.0.
            if confidence > 1:
                confidence = confidence / 100
            confidence = round(confidence, 2)
            is_tut_proof = confidence >= 0.8
            return {
                'document_type': 'TUT Proof of Registration' if is_tut_proof else 'unknown',
                'is_tut_proof_of_registration': is_tut_proof,
                'classification_confidence': confidence,
                'classification_reason': llm_result.get('classification_reason') or (
                    'Document contains the expected TUT proof-of-registration wording.'
                    if is_tut_proof
                    else 'Document does not match the TUT proof-of-registration pattern closely enough.'
                ),
            }

    # LLM unavailable, failed, or returned no usable confidence - fall back to keyword matching.
    return classify_tut_proof_of_registration(extracted_text)


def calculate_confidence_score(result, used_llm, ocr_engine_confidence=0.0):
    """Compute a confidence score that keeps valid TUT extracts above the verification threshold."""
    key_fields = [
        result.get('student_number'),
        result.get('full_name') or result.get('student_name'),
        result.get('university_name'),
    ]

    if all(key_fields):
        base = 0.95 if used_llm else 0.9
    elif result.get('student_number') and (result.get('full_name') or result.get('student_name')):
        base = 0.8
    elif result.get('student_number') or result.get('full_name') or result.get('student_name'):
        base = 0.6
    else:
        return 0.0

    if ocr_engine_confidence <= 0:
        # Text came from a PDF's selectable text layer (no OCR ran), which is
        # already reliable - nothing to scale against.
        return base

    # Scale down when Tesseract itself wasn't confident in the scanned text,
    # so a lucky regex/LLM match on garbled text isn't reported as high confidence.
    engine_factor = min(1.0, ocr_engine_confidence / 80)
    return round(base * engine_factor, 2)


def is_tut_registration_document(text):
    """Check whether the uploaded document appears to be from TUT."""
    classification = classify_tut_proof_of_registration(text)
    return classification['is_tut_proof_of_registration']


def parse_registration_end_date(period):
    """Parse the end date out of an extracted 'X to Y' registration period.

    Accepts the formats produced by both the regex and LLM extractors
    (e.g. "03-Jan-2026 to 31-Dec-2026" or "2023 to 2024"). Returns None
    if nothing usable is found, so callers can fall back to a default.
    """
    if not period:
        return None

    match = re.search(r'to\s+(.+)$', period, re.IGNORECASE)
    end_text = match.group(1).strip() if match else period.strip()

    date_formats = ('%d-%b-%Y', '%d %B %Y', '%Y-%m-%d', '%d/%m/%Y', '%Y')
    for date_format in date_formats:
        try:
            parsed = datetime.strptime(end_text, date_format)
        except ValueError:
            continue
        if date_format == '%Y':
            return datetime(parsed.year, 12, 31).date()
        return parsed.date()

    return None


def extract_pdf_text(pdf_path):
    """Extract selectable text from a PDF before falling back to OCR."""
    if fitz is None:
        return ""

    try:
        document = fitz.open(pdf_path)
        extracted_pages = []
        for page in document:
            page_text = page.get_text('text') or ''
            if page_text:
                extracted_pages.append(page_text)
        document.close()
        return '\n'.join(extracted_pages)
    except Exception as e:
        print(f"PDF text extraction error: {str(e)}")
        return ""


def render_pdf_pages(pdf_path):
    """Render PDF pages to PIL images for OCR fallback."""
    if fitz is None:
        return []

    document = fitz.open(pdf_path)
    images = []
    try:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_mode = 'RGB' if pixmap.n < 4 else 'RGBA'
            image = Image.frombytes(image_mode, [pixmap.width, pixmap.height], pixmap.samples)
            images.append(image.convert('RGB'))
    finally:
        document.close()

    return images


def build_card_verification_url(card_number, request=None):
    """Build the URL a scanned QR code should open to show the card."""
    from django.urls import reverse
    path = reverse('verify_card_public', args=[card_number])
    return request.build_absolute_uri(path) if request is not None else path


def link_recent_anonymous_visits(user, request, within_minutes=60):
    """Attribute a brand-new user's pre-signup page visits (e.g. viewing the
    signup form before an account existed) to their new account, matched by
    IP address within a recent time window. Returns how many rows were linked.
    """
    from .models import PageVisit

    ip_address = request.META.get('REMOTE_ADDR')
    if not ip_address:
        return 0

    cutoff = timezone.now() - timedelta(minutes=within_minutes)
    return PageVisit.objects.filter(
        user__isnull=True,
        ip_address=ip_address,
        timestamp__gte=cutoff,
    ).update(user=user)


def generate_unique_card_number(user):
    return f"TUT{user.id}{uuid.uuid4().hex[:8].upper()}"


def generate_qr_code_for_card(student_card, request=None):
    """Generate and save the verification QR code for a card that already
    has a card_number - shared by generate_student_card() and by
    StudentCardAdmin for manually-added cards, so both paths produce the
    same kind of QR code.
    """
    from django.core.files.base import ContentFile

    qr_data = build_card_verification_url(student_card.card_number, request)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")

    # Save QR code through the FileField's own storage API (not a raw
    # MEDIA_ROOT path) so this lands in the configured backend - local
    # disk in dev, but S3/Spaces in production, where a container-local
    # file would never actually be reachable at the saved URL.
    qr_filename = f"qr_{student_card.card_number}.png"
    buffer = io.BytesIO()
    qr_image.save(buffer, format='PNG')
    student_card.qr_code.save(qr_filename, ContentFile(buffer.getvalue()), save=False)
    student_card.save()


def generate_student_card(user, request=None):
    """
    Generate a digital student card for a verified user.
    Creates card record and QR code that links to the card's public
    verification page, so scanning it shows the card itself.
    """
    from .models import StudentCard, CardActivity

    try:
        # Check if card already exists
        student_card = StudentCard.objects.filter(user=user).first()
        if not student_card:
            # Card expiry follows the student's registration period when the
            # proof of registration gave us one; otherwise default to 1 year.
            expiry_date = user.registration_end_date or (datetime.now().date() + timedelta(days=365))

            # Create card record
            student_card = StudentCard.objects.create(
                user=user,
                card_number=generate_unique_card_number(user),
                expiry_date=expiry_date,
                status='active'
            )

            generate_qr_code_for_card(student_card, request)

            # Log activity
            CardActivity.objects.create(
                student_card=student_card,
                activity_type='generated'
            )

        return student_card

    except Exception as e:
        print(f"Error generating student card: {str(e)}")
        raise


def get_card_display_data(student_card):
    """
    Prepare card data for display (Web Pass format).
    """
    user = student_card.user
    return {
        'card_number': student_card.card_number,
        'student_number': user.student_number,
        'full_name': user.get_full_name_display(),
        'email': user.email,
        'phone': user.phone,
        'issue_date': student_card.issue_date.strftime('%Y-%m-%d'),
        'expiry_date': student_card.expiry_date.strftime('%Y-%m-%d') if student_card.expiry_date else None,
        'status': student_card.status,
        'is_valid': student_card.is_valid(),
        'qr_code_url': student_card.qr_code.url if student_card.qr_code else None,
        'profile_picture_url': user.profile_picture.url if user.profile_picture else None,
    }
