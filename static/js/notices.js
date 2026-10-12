// Messages from the card office (dashboard): one message at a time, swiped
// sideways or moved with the arrows, with dots and "1 of 2" showing where
// you are. Once the panel has been on screen for a moment the messages count
// as read, which clears the "new" counts on the panel and on the bell.
(function () {
    var panel = document.getElementById('messages');
    if (!panel) {
        return;
    }
    var strip = panel.querySelector('.notice-carousel');
    var slides = Array.prototype.slice.call(strip.children);
    var prev = panel.querySelector('.notice-prev');
    var next = panel.querySelector('.notice-next');
    var dots = panel.querySelector('.notice-dots');
    var position = panel.querySelector('.notice-position');

    slides.forEach(function () { dots.appendChild(document.createElement('span')); });

    function current() {
        return Math.round(strip.scrollLeft / Math.max(strip.clientWidth, 1));
    }

    function update() {
        var index = Math.min(current(), slides.length - 1);
        Array.prototype.forEach.call(dots.children, function (dot, i) {
            dot.classList.toggle('active', i === index);
        });
        position.textContent = slides.length > 1 ? (index + 1) + ' of ' + slides.length : '';
        prev.disabled = index === 0;
        next.disabled = index >= slides.length - 1;
    }

    function go(step) {
        var index = Math.max(0, Math.min(current() + step, slides.length - 1));
        strip.scrollTo({ left: index * strip.clientWidth });
    }

    prev.addEventListener('click', function () { go(-1); });
    next.addEventListener('click', function () { go(1); });
    strip.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowRight') { go(1); event.preventDefault(); }
        if (event.key === 'ArrowLeft') { go(-1); event.preventDefault(); }
    });
    var settle = null;
    strip.addEventListener('scroll', function () {
        clearTimeout(settle);
        settle = setTimeout(update, 60);
    });
    window.addEventListener('resize', update);
    if (slides.length < 2) {
        prev.hidden = next.hidden = true;
    }
    update();

    // Read once seen: on screen for a second and a half.
    var marked = false;
    function markRead() {
        if (marked || !document.querySelector('.notice-count')) {
            return;
        }
        marked = true;
        fetch(panel.dataset.readUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': panel.dataset.csrf },
            credentials: 'same-origin'
        }).then(function (response) {
            if (!response.ok) {
                marked = false;
                return;
            }
            document.querySelectorAll('.notice-count').forEach(function (badge) { badge.remove(); });
        }).catch(function () { marked = false; });
    }
    if ('IntersectionObserver' in window) {
        var timer = null;
        new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                clearTimeout(timer);
                if (entry.isIntersecting) {
                    timer = setTimeout(markRead, 1500);
                }
            });
        }, { threshold: 0.6 }).observe(panel);
    }
})();
