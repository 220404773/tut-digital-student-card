// Pop-up messages ("toasts"), styled like the "Install TUT Card" pop-up:
// a card in a bottom corner (the top on a phone), floating over the page so
// nothing below them moves. Used for Django's messages (rendered by
// templates/base.html into #toast-stack) and by page scripts through
// window.showToast(level, text, {timeout: ms}) - a timeout of 0 keeps the
// toast up until closed, or until the page calls toast.dismiss().
//
// Every toast closes by itself; warnings and errors stay longer, because
// those usually say what to do next.
// Hovering over or focusing a toast pauses its timer.
(function () {
    var TIMEOUTS = { success: 5000, info: 8000, warning: 12000, danger: 12000 };
    var TITLES = { success: 'Done', info: 'Notice', warning: 'Please check', danger: 'Something went wrong' };
    var ICONS = {
        success: 'fa-circle-check',
        info: 'fa-circle-info',
        warning: 'fa-triangle-exclamation',
        danger: 'fa-circle-xmark'
    };

    function stack() {
        var el = document.getElementById('toast-stack');
        if (!el) {
            el = document.createElement('div');
            el.id = 'toast-stack';
            el.className = 'toast-stack';
            document.body.appendChild(el);
        }
        return el;
    }

    function close(toast) {
        if (toast.dataset.closing) {
            return;
        }
        toast.dataset.closing = '1';
        toast.classList.add('app-toast-leaving');
        // Removed after the slide-out; straight away if animations are off.
        var removed = false;
        function remove() {
            if (!removed) {
                removed = true;
                toast.remove();
            }
        }
        toast.addEventListener('animationend', remove);
        setTimeout(remove, 400);
    }

    function start(toast, timeoutOverride) {
        var level = toast.dataset.level;
        var timeout = TIMEOUTS.hasOwnProperty(level) ? TIMEOUTS[level] : 8000;
        if (typeof timeoutOverride === 'number') {
            timeout = timeoutOverride;
        }
        toast.dismiss = function () { close(toast); };
        var closeButton = toast.querySelector('.app-toast-close');
        if (closeButton) {
            closeButton.addEventListener('click', function () { close(toast); });
        }
        if (!timeout) {
            return;
        }
        var timer = toast.querySelector('.app-toast-timer');
        var remaining = timeout;
        var startedAt = 0;
        var handle = null;

        function run() {
            startedAt = Date.now();
            handle = setTimeout(function () { close(toast); }, remaining);
            if (timer) {
                timer.style.animationDuration = timeout + 'ms';
                timer.style.animationPlayState = 'running';
            }
        }
        function pause() {
            if (handle === null) {
                return;
            }
            clearTimeout(handle);
            handle = null;
            remaining -= Date.now() - startedAt;
            if (timer) {
                timer.style.animationPlayState = 'paused';
            }
        }
        toast.addEventListener('mouseenter', pause);
        toast.addEventListener('focusin', pause);
        toast.addEventListener('mouseleave', function () { if (handle === null) { run(); } });
        toast.addEventListener('focusout', function () { if (handle === null) { run(); } });
        toast.classList.add('app-toast-timed');
        run();
    }

    function showToast(level, text, options) {
        if (!TITLES.hasOwnProperty(level)) {
            level = 'info';
        }
        // Same structure as the server-rendered toasts in base.html, built
        // from Bootstrap's card classes like the "Install TUT Card" pop-up.
        var toast = document.createElement('div');
        toast.className = 'app-toast app-toast-' + level + ' card shadow';
        toast.dataset.level = level;
        // Warnings and errors are announced straight away by screen readers.
        toast.setAttribute('role', level === 'warning' || level === 'danger' ? 'alert' : 'status');

        var body = document.createElement('div');
        body.className = 'card-body d-flex gap-3 align-items-start';

        var iconBox = document.createElement('span');
        iconBox.className = 'app-toast-icon';
        iconBox.setAttribute('aria-hidden', 'true');
        var icon = document.createElement('i');
        icon.className = 'fas ' + ICONS[level];
        iconBox.appendChild(icon);

        var textBox = document.createElement('div');
        textBox.className = 'flex-grow-1';
        var title = document.createElement('h6');
        title.className = 'mb-1';
        title.textContent = TITLES[level];
        var message = document.createElement('p');
        message.className = 'mb-0 app-toast-text';
        message.textContent = text;  // text, never HTML
        textBox.appendChild(title);
        textBox.appendChild(message);

        var closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'btn-close app-toast-close';
        closeButton.setAttribute('aria-label', 'Dismiss this message');

        body.appendChild(iconBox);
        body.appendChild(textBox);
        body.appendChild(closeButton);

        var timer = document.createElement('div');
        timer.className = 'app-toast-timer';

        toast.appendChild(body);
        toast.appendChild(timer);
        stack().appendChild(toast);
        start(toast, options && options.timeout);
        return toast;
    }

    window.showToast = showToast;

    function init() {
        document.querySelectorAll('#toast-stack .app-toast').forEach(function (toast) { start(toast); });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
