// For forms whose submit takes a while (reading a document can take several
// seconds): once submitted, the button is disabled and says what is
// happening, so it isn't clicked again and the page doesn't look stuck.
// Usage: <form data-busy-text="Checking your document...">
document.addEventListener('submit', function (event) {
    var form = event.target;
    var text = form.getAttribute('data-busy-text');
    if (!text || event.defaultPrevented) {
        return;
    }
    var button = event.submitter || form.querySelector('[type="submit"]');
    if (!button) {
        return;
    }
    // Disabled after this tick, so the button still submits with the form.
    setTimeout(function () {
        button.disabled = true;
        button.textContent = '';
        var spinner = document.createElement('span');
        spinner.className = 'spinner-border spinner-border-sm me-2';
        spinner.setAttribute('aria-hidden', 'true');
        button.appendChild(spinner);
        button.appendChild(document.createTextNode(text));
    }, 0);
});
