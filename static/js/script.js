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

    // Results appear as pop-up messages (static/js/toasts.js), so the form
    // doesn't jump around while the student is filling it in.
    function toast(level, text, options) {
        if (window.showToast) {
            return window.showToast(level, text, options);
        }
        return null;
    }

    let readingToast = null;
    function finishReading() {
        if (readingToast && readingToast.dismiss) {
            readingToast.dismiss();
        }
        readingToast = null;
    }

    fileInput.addEventListener('change', function(event) {
        const MAX_SIZE = 5 * 1024 * 1024;
        const ALLOWED = ['application/pdf', 'image/jpeg', 'image/png'];
        const previewUrl = fileInput.dataset.previewUrl;
        const file = event.target.files && event.target.files[0];

        if (!file) {
            return;
        }

        if (file.size > MAX_SIZE) {
            toast('warning', 'This file is too large - the limit is 5 MB.');
            event.target.value = '';
            return;
        }

        if (!ALLOWED.includes(file.type)) {
            toast('warning', 'This file type is not allowed. Upload a PDF, JPG or PNG.');
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

        finishReading();
        readingToast = toast('info', 'Reading your document and filling in your details...', { timeout: 0 });

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
            finishReading();

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
                    toast('success', `${documentType} read successfully - your details have been filled in. Please check them.`);
                } else {
                    toast('warning', `${documentType} received, but no details could be read. Please enter your details yourself.`);
                }
                return;
            }

            // Rejected (an old or wrong document): clear the file so it's
            // obvious it won't be submitted, and say why.
            fileInput.value = '';
            toast('danger', data.message || 'We could not read this document. Please try another copy.');
        })
        .catch(function() {
            finishReading();
            toast('warning', 'Your document could not be checked right now. Please enter your details yourself.');
        });
    });
});
