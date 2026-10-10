// Asks for confirmation before submitting any form with a data-confirm
// attribute. Replaces inline onsubmit="return confirm('...')" handlers, which
// the Content-Security-Policy blocks - and which were unsafe anyway, since a
// student's name placed inside a JavaScript string in an HTML attribute is
// decoded back to raw quotes before the script runs.
// The clicked button's own data-confirm wins, so one form can have a
// confirmed action (Decline) next to an unconfirmed one (Approve).
document.addEventListener('submit', function (event) {
    var button = event.submitter;
    var message = (button && button.getAttribute('data-confirm')) || event.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) {
        event.preventDefault();
    }
});
