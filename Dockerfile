FROM python:3.12-slim

# Otherwise print()-based error logging (used throughout the OCR pipeline)
# sits in stdout's block buffer and never reaches `doctl apps logs`.
ENV PYTHONUNBUFFERED=1

# Tesseract for OCR, libgl1/libglib2.0-0 for OpenCV (deskew/binarize
# preprocessing), build-essential/libpq-dev so psycopg2-binary can build
# from source if no prebuilt wheel matches this image (it doesn't always
# ship one) - build-essential (not just gcc) is needed for the full libc
# headers (stdlib.h etc.), which gcc alone doesn't pull in on this base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python manage.py collectstatic --noinput

EXPOSE 8080
CMD ["gunicorn", "tut_student_card.wsgi", "--bind=0.0.0.0:8080"]
