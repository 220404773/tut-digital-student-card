// The live card: what makes the card on a phone hard to fake with a
// screenshot or a recording of someone else's card.
//   - a ticking clock and date (a screenshot freezes, a recording shows the
//     wrong time);
//   - a shimmer that follows the phone's tilt where the browser allows it;
//   - a QR code redrawn every few seconds. Each one carries a signed code that
//     expires, so the verification page reports an old one as a screenshot.
// Loaded from a static file, so the Content-Security-Policy needs no
// exceptions.
(function () {
    'use strict';

    // -- clock --------------------------------------------------------------
    var clocks = document.querySelectorAll('[data-live-clock]');
    var dates = document.querySelectorAll('[data-live-date]');

    function tick() {
        var now = new Date();
        var time = now.toLocaleTimeString('en-ZA', {hour12: false});
        var date = now.toLocaleDateString('en-ZA', {day: '2-digit', month: 'short', year: 'numeric'});
        clocks.forEach(function (el) { el.textContent = time; el.setAttribute('datetime', now.toISOString()); });
        dates.forEach(function (el) { el.textContent = date; });
    }
    if (clocks.length) {
        tick();
        setInterval(tick, 1000);
    }

    // -- live QR code -------------------------------------------------------
    var qr = document.getElementById('card-qr');
    var qrFailed = document.getElementById('card-qr-failed');

    if (qr) {
        var seconds = parseInt(qr.dataset.refreshSeconds, 10) || 30;
        var base = qr.dataset.baseSrc || qr.src;

        var showFallback = function () {
            qr.classList.add('d-none');
            if (qrFailed) { qrFailed.classList.remove('d-none'); }
        };
        var hideFallback = function () {
            qr.classList.remove('d-none');
            if (qrFailed) { qrFailed.classList.add('d-none'); }
        };
        var refresh = function () {
            // A new address each time, so nothing in between serves an old code.
            qr.src = base + (base.indexOf('?') === -1 ? '?' : '&') + 't=' + Date.now();
        };

        qr.addEventListener('error', showFallback);
        // A later refresh that works brings the code back after a dropped connection.
        qr.addEventListener('load', hideFallback);
        // It may already have failed before this script ran.
        if (qr.complete && qr.naturalWidth === 0) { showFallback(); }

        setInterval(refresh, seconds * 1000);
        // Phones pause timers while the screen is off: show a fresh code as
        // soon as the card is opened again.
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') { refresh(); }
        });
    }

    // -- tilt shimmer -------------------------------------------------------
    var card = document.querySelector('[data-live-card]');
    var tiltButton = document.querySelector('[data-enable-tilt]');
    var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (card && !reducedMotion && window.DeviceOrientationEvent) {
        var onTilt = function (event) {
            if (event.gamma === null || event.beta === null) { return; }
            // Left-right tilt moves the shine across; front-back moves it up and down.
            var x = Math.max(-40, Math.min(40, event.gamma));
            var y = Math.max(-40, Math.min(40, event.beta - 45));
            card.style.setProperty('--tilt-x', (50 + x * 1.2) + '%');
            card.style.setProperty('--tilt-y', (50 + y * 1.2) + '%');
            card.classList.add('tilt-active');
        };
        var listen = function () { window.addEventListener('deviceorientation', onTilt); };

        // Phones and tablets only: their main pointer is a finger. Computers -
        // touchscreen laptops included - have no motion sensor worth asking for.
        var isHandheld = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;

        if (typeof DeviceOrientationEvent.requestPermission === 'function') {
            // iPhones only allow motion sensors after a tap; until then the
            // shimmer moves on its own.
            if (tiltButton && isHandheld) {
                tiltButton.classList.remove('d-none');
                tiltButton.addEventListener('click', function () {
                    DeviceOrientationEvent.requestPermission().then(function (state) {
                        if (state === 'granted') { listen(); }
                        tiltButton.classList.add('d-none');
                    }).catch(function () { tiltButton.classList.add('d-none'); });
                });
            }
        } else {
            listen();
        }
    }
})();
