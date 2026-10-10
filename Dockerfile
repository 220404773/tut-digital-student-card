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

# Settings refuse to load without a real SECRET_KEY when DEBUG is off, and
# no runtime variables exist at build time. collectstatic only copies files
# and never signs anything, so it gets a throwaway key that exists only for
# this step - the running app uses the SECRET_KEY set on App Platform.
RUN SECRET_KEY=build-step-only-collectstatic-never-signs-anything python manage.py collectstatic --noinput

EXPOSE 8080
# Default gunicorn worker timeout (30s) was killing requests mid-OCR - image
# preprocessing (OpenCV) + Tesseract + the Azure OpenAI call routinely take
# longer than that on this instance size.
# Migrations run on every start, before Gunicorn: a deploy that adds a model
# (e.g. ReviewRequest) then can't serve pages against a database that doesn't
# have its table yet. Safe to repeat - migrate does nothing when up to date -
# and the app runs as a single instance, so two containers never race.
# createcachetable makes the database cache's table (rate-limit and
# failed-login counts, settings.CACHES); it also does nothing if it exists.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py createcachetable && exec gunicorn tut_student_card.wsgi --bind=0.0.0.0:8080 --timeout=120"]
