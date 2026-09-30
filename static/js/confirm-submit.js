// Asks for confirmation before submitting any form with a data-confirm
// attribute. Replaces inline onsubmit="return confirm('...')" handlers, which
// the Content-Security-Policy blocks - and which were unsafe anyway, since a
// student's name placed inside a JavaScript string in an HTML attribute is
// decoded back to raw quotes before the script runs.
document.addEventListener('submit', function (event) {
    var message = event.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
        event.preventDefault();
    }
});
