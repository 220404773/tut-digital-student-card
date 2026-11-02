// Reloads the student home page by itself once what it is waiting on happens:
// the email confirmation link clicked (often in another tab or on a phone),
// the registration approved by staff, or the card issued.
// Checks every 5 seconds while the page is on screen, and gives up after
// 10 minutes so the idle sign-out still applies; coming back to the tab
// checks straight away and starts the 10 minutes again.
(function () {
    var box = document.querySelector('[data-status-url]');
    if (!box || !window.fetch) {
        return;
    }
    var url = box.dataset.statusUrl;
    var shown = box.dataset.status;
    var WAIT = 5000;
    var LIMIT = 10 * 60 * 1000;
    var stopAt = Date.now() + LIMIT;
    var timer = null;
    var bit = function (value) { return value ? '1' : '0'; };

    function later() {
        timer = Date.now() < stopAt ? setTimeout(check, WAIT) : null;
    }

    function check() {
        timer = null;
        if (document.hidden) {
            return;  // resumed by visibilitychange
        }
        fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (data) {
                if (data && bit(data.email_confirmed) + bit(data.is_verified) + bit(data.has_card) !== shown) {
                    window.location.reload();
                } else {
                    later();
                }
            })
            .catch(later);
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            stopAt = Date.now() + LIMIT;
            clearTimeout(timer);
            check();
        }
    });
    later();
})();
