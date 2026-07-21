# TUT Digital Student Card System

A simple digital student card application for Tshwane University of Technology. Students can create digital ID cards, download QR codes, and track their card usage.

## Features

- **User Registration**: Sign up with student number and upload registration proof
- **Digital Card**: Auto-generated card with unique number
- **QR Code**: Download QR code for campus verification
- **Activity Log**: Track when card is viewed or downloaded
- **Student Search**: Find and view other verified students
- **Mobile Friendly**: Works on phones and tablets
- **Admin Panel**: Manage users and cards

## Project Structure

```
tut_student_card/
├── accounts/                          # User authentication app
│   ├── models.py                     # CustomUser model with OCR fields
│   ├── forms.py                      # Signup, login, profile forms
│   ├── views.py                      # Authentication views
│   ├── urls.py                       # URL routing
│   ├── admin.py                      # Django admin configuration
│   ├── templates/accounts/
│   │   ├── signup.html               # Registration form
│   │   ├── login.html                # Login form
│   │   ├── profile.html              # User profile management
│   │   ├── search.html               # Student directory search
│   │   └── student_detail.html       # Public student profiles
│   └── apps.py
├── cards/                             # Digital card app
│   ├── models.py                     # StudentCard, CardActivity, CardDownload
│   ├── views.py                      # Card display and download views
│   ├── urls.py                       # Card URL routing
│   ├── utils.py                      # OCR processing & QR generation
│   ├── admin.py                      # Card admin interfaces
│   ├── templates/cards/
│   │   ├── dashboard.html            # Main card dashboard
│   │   ├── card.html                 # Full card display
│   │   ├── activity_log.html         # Activity tracking
│   │   ├── verify.html               # Card verification
│   └── apps.py
├── tut_student_card/
│   ├── settings.py                   # Django configuration
│   ├── urls.py                       # Main URL router
│   ├── asgi.py                       # ASGI config
│   └── wsgi.py                       # WSGI config
├── templates/
│   ├── base.html                     # Base template with TUT branding
│   └── index.html                    # Home page
├── static/
│   ├── css/
│   │   └── style.css                 # Custom styling
│   └── js/
│       └── script.js                 # Frontend JavaScript
├── media/                            # User uploads and QR codes
├── manage.py                         # Django management script
├── requirements.txt                  # Python dependencies
├── .env.example                      # Environment template
├── .gitignore                        # Git ignore rules
├── QUICKSTART.md                     # Quick setup guide
└── README.md                         # This file
```

## Installation

### Prerequisites
- Python 3.9+
- pip (Python package manager)
- PostgreSQL (for production) or SQLite (for development)
- Tesseract OCR (optional, for document scanning)

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/tut/digital-student-card.git
   cd digital-student-card
   ```

2. **Create virtual environment**
   ```bash
   python -m venv myenv
   source myenv/bin/activate  # On Windows: myenv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

5. **Run migrations**
   ```bash
   python manage.py makemigrations accounts
   python manage.py makemigrations cards
   python manage.py migrate
   ```

6. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

7. **Collect static files**
   ```bash
   python manage.py collectstatic --noinput
   ```

8. **Run development server**
   ```bash
   python manage.py runserver
   ```

9. **Access application**
   - Home: http://localhost:8000/
   - Admin: http://localhost:8000/admin/
   - Sign up: http://localhost:8000/accounts/signup/

## Configuration

### Environment Variables (.env)

```env
# Django
DEBUG=True
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=localhost,127.0.0.1

# Database (development)
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3

# Database (production - PostgreSQL)
DB_ENGINE=django.db.backends.postgresql
DB_NAME=tut_student_card
DB_USER=postgres
DB_PASSWORD=your-password
DB_HOST=localhost
DB_PORT=5432

# OCR Settings
OCR_ENABLED=True
OCR_CONFIDENCE_THRESHOLD=0.8

# Email Configuration
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password

# AWS S3 (optional, for file storage)
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
AWS_STORAGE_BUCKET_NAME=your-bucket
```

### Installing OCR (Optional)

For automatic document scanning capabilities:

**Windows:**
```bash
# Download installer from: https://github.com/UB-Mannheim/tesseract/wiki
# Then install Python dependencies:
pip install pytesseract pdf2image
```

**macOS:**
```bash
brew install tesseract
pip install pytesseract pdf2image
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install tesseract-ocr
pip install pytesseract pdf2image
```

## Usage

### Student Registration Flow

1. **Sign Up**
   - Student visits signup page
   - Provides email, name, student number, phone, DOB, gender
   - Uploads proof of registration (PDF/JPG/PNG, max 5MB)
   - Creates password
   - System auto-processes document via OCR
   - Student auto-logged in after verification

2. **Dashboard Access**
   - View digital student card
   - Check verification status
   - Download QR code
   - View activity log

3. **Card Verification**
   - Display QR code to security personnel
   - Campus systems scan QR to verify student
   - Activity logged for audit trail

### Admin Features

- Manage users and student accounts
- View card activity logs
- Monitor verification status
- Generate reports
- Configure system settings

## Database Schema

### CustomUser Model
- email (unique)
- first_name, last_name
- student_number (unique)
- phone, date_of_birth
- gender (M/F/O)
- registration_proof (file)
- is_verified (boolean)
- OCR fields: ocr_student_number, ocr_full_name, ocr_academic_year, ocr_registration_status, ocr_confidence_score

### StudentCard Model
- card_number (unique UUID)
- user (OneToOne with CustomUser)
- qr_code (image)
- status (active/inactive/revoked/expired)
- issue_date, expiry_date

### CardActivity Model
- student_card (ForeignKey)
- activity_type (viewed/downloaded/verified/generated)
- timestamp
- ip_address
- user_agent

## Security Features

### Authentication
- Email-based login with password hashing (PBKDF2)
- CSRF protection on all forms
- Session management with timeout

### Data Protection
- HTTPS in production (enforced via SECURE_SSL_REDIRECT)
- Password minimum requirements
- File upload validation (type and size)
- POPIA compliance: automatic document deletion after OCR

### Audit Trail
- Complete activity logging
- IP address tracking
- User agent logging
- Timestamp recording for all actions

### File Storage
- Uploaded files stored separately from code
- Media files served via Django with access control
- Automatic cleanup of processed documents

## API Endpoints

### Authentication
- `POST /accounts/signup/` - Register new student
- `POST /accounts/login/` - Student login
- `GET /accounts/logout/` - Student logout

### User Management
- `GET /accounts/profile/` - View profile
- `POST /accounts/profile/` - Update profile
- `GET /accounts/search/` - Search students
- `GET /accounts/student/<id>/` - View student profile

### Card Operations
- `GET /cards/dashboard/` - Main dashboard
- `GET /cards/card/` - View student card
- `GET /cards/card/download/` - Download QR code
- `GET /cards/card/activity/` - View activity log
- `GET /cards/verify/` - Card verification

## Testing

```bash
# Run all tests
python manage.py test

# Run specific app tests
python manage.py test accounts
python manage.py test cards

# With coverage report
coverage run --source='.' manage.py test
coverage report
```

## Deployment

### Production Checklist

1. **Security**
   - Set `DEBUG=False`
   - Update `SECRET_KEY` to random value
   - Configure `ALLOWED_HOSTS` properly
   - Enable HTTPS/SSL
   - Set secure cookies: `SESSION_COOKIE_SECURE=True`

2. **Database**
   - Use PostgreSQL instead of SQLite
   - Configure connection pooling
   - Regular backups

3. **Static Files**
   - Collect static files: `python manage.py collectstatic`
   - Serve via CDN or web server (nginx/Apache)

4. **Email**
   - Configure production email backend
   - Setup email service (SendGrid, AWS SES, etc.)

5. **File Storage**
   - Use AWS S3 or similar for media files
   - Configure CORS properly

6. **Monitoring**
   - Setup error tracking (Sentry)
   - Monitor logs and performance
   - Setup uptime monitoring

## Troubleshooting

### OCR Not Working
- Check if Tesseract is installed: `tesseract --version`
- Verify `OCR_ENABLED=True` in .env
- Check uploaded file format and quality
- Review logs for error messages

### Database Errors
- Clear migrations: `python manage.py migrate accounts zero`
- Recreate migrations: `python manage.py makemigrations`
- Run migrate: `python manage.py migrate`

### File Upload Issues
- Check file size (max 5MB)
- Verify file format (PDF, JPG, PNG)
- Ensure media directory is writable

### Static Files Not Loading
- Run: `python manage.py collectstatic`
- Check `STATIC_ROOT` and `STATIC_URL` settings
- Verify web server configuration

## Performance Optimization

1. **Database**
   - Add indexes on frequently queried fields
   - Use select_related() and prefetch_related()
   - Enable query caching

2. **Caching**
   - Cache static assets
   - Cache QR code generation
   - Use Redis for session storage

3. **Images**
   - Optimize QR code generation
   - Compress uploaded images
   - Use CDN for media delivery

## Support

- **Documentation**: See QUICKSTART.md for quick setup
- **Issues**: Report bugs via GitHub Issues
- **Email**: support@tut.ac.za
- **Phone**: +27 (0)12 382 6000

## License

This project is proprietary software of Tshwane University of Technology. All rights reserved.

## Contributors

- Development Team: TUT ICT Services
- Project Manager: Digital Transformation Office
- Contact: ict.services@tut.ac.za

## Roadmap

### Phase 1 (Current - July 2026)
- ✅ Backend API development
- ✅ Frontend templates
- ✅ OCR integration
- ✅ QR code generation
- 🔄 Testing and bug fixes

### Phase 2 (August 2026)
- Google Wallet integration
- Mobile app development
- Advanced reporting
- Performance optimization

### Phase 3 (September 2026 - Production)
- Full production deployment
- Load testing
- Security audit
- User training

## Changelog

### v1.0.0 (2026-07-XX)
- Initial release
- Core authentication system
- Digital card generation
- OCR document processing
- QR code verification
- Activity audit logging

---

**Version**: 1.0.0  
**Last Updated**: 2026-07-XX  
**Status**: Development
