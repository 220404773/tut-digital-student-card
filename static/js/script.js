// TUT Student Card - Simple JavaScript

document.addEventListener('DOMContentLoaded', function() {
    const fileInput = document.getElementById('id_registration_proof');

    // Signup submission can take a while (OCR + LLM classification) - without
    // this, an impatient double-click submits the form twice, which can
    // create two accounts/cards or send the confirmation email twice.
    const signupForm = document.querySelector('form[enctype="multipart/form-data"]');
    if (signupForm) {
        signupForm.addEventListener('submit', function() {
            const submitButton = signupForm.querySelector('button[type="submit"]');
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating your account...';
            }
        });
    }

    if (!fileInput) {
        return;
    }

    fileInput.addEventListener('change', function(event) {
        const MAX_SIZE = 5 * 1024 * 1024;
        const ALLOWED = ['application/pdf', 'image/jpeg', 'image/png'];
        const previewUrl = fileInput.dataset.previewUrl;
        const statusBox = document.getElementById('ocr-preview-status');
        const file = event.target.files && event.target.files[0];

        if (!file) {
            return;
        }

        if (file.size > MAX_SIZE) {
            alert('File too large (max 5MB)');
            event.target.value = '';
            return;
        }

        if (!ALLOWED.includes(file.type)) {
            alert('File type not allowed (PDF, JPG, PNG only)');
            event.target.value = '';
            return;
        }

        // Marks the real start of "signup time" for the admin metrics - a
        // page refresh wipes this hidden field along with everything else,
        // so an abandoned attempt is never counted.
        const uploadStartField = document.getElementById('id_upload_start_time');
        if (uploadStartField) {
            uploadStartField.value = Date.now();
        }

        if (!previewUrl) {
            return;
        }

        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
        const formData = new FormData();
        formData.append('registration_proof', file);

        if (statusBox) {
            statusBox.className = 'alert alert-info';
            statusBox.textContent = 'Processing uploaded file and extracting your details...';
            statusBox.classList.remove('d-none');
        }

        fetch(previewUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
            },
            body: formData,
        })
        .then(function(response) {
            return response.json();
        })
        .then(function(data) {
            if (!statusBox) {
                return;
            }

            if (data.success) {
                const email = document.getElementById('id_email');
                const studentNumber = document.getElementById('id_student_number');
                const firstName = document.getElementById('id_first_name');
                const surname = document.getElementById('id_last_name');

                let fieldsPopulated = false;

                if (email && data.email) {
                    email.value = data.email;
                    fieldsPopulated = true;
                }

                if (studentNumber && data.student_number) {
                    studentNumber.value = data.student_number;
                    fieldsPopulated = true;
                }

                if (firstName && data.first_name) {
                    firstName.value = data.first_name;
                    fieldsPopulated = true;
                }

                if (surname && data.last_name) {
                    surname.value = data.last_name;
                    fieldsPopulated = true;
                }

                const documentType = data.document_type || 'TUT Proof of Registration';

                if (fieldsPopulated) {
                    statusBox.className = 'alert alert-success';
                    statusBox.textContent = `${documentType} processed successfully. The required fields have been populated automatically.`;
                } else {
                    statusBox.className = 'alert alert-warning';
                    statusBox.textContent = `${documentType} received, but no details could be extracted. Please enter your details manually.`;
                }
                return;
            }

            // Invalid document (e.g. failed classification) - clear the
            // file input so it's obvious that file was rejected, not still
            // queued for submission.
            fileInput.value = '';
            statusBox.className = 'alert alert-warning';
            statusBox.textContent = data.message || 'We could not extract details from this document. Please type them manually.';
        })
        .catch(function() {
            if (statusBox) {
                statusBox.className = 'alert alert-warning';
                statusBox.textContent = 'Document preview failed. Please enter the details manually.';
            }
        });
    });
});
