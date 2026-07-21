// TUT Student Card - Simple JavaScript

document.addEventListener('DOMContentLoaded', function() {
    const fileInput = document.getElementById('id_registration_proof');

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

// Copy text to clipboard
function copyToClipboard(text) {
    navigator.clipboard.writeText(text);
    alert('Copied!');
}

// Print card
function printCard() {
    window.print();
}
