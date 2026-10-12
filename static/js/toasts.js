// Pop-up messages ("toasts") in a corner of the screen, floating over the
// page so nothing below them moves. Used for Django's messages (rendered by
// templates/base.html into #toast-stack) and by page scripts through
// window.showToast(level, text, {timeout: ms}) - a timeout of 0 keeps the
// toast up until closed, or until the page calls toast.dismiss().
//
// Success and information close by themselves; a warning stays longer and an
// error stays until it is closed, because those usually say what to do next.
// Hovering over or focusing a toast pauses its timer.
(function () {
    var TIMEOUTS = { success: 5000, info: 8000, warning: 12000, danger: 0 };
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
        var toast = document.createElement('div');
        toast.className = 'app-toast app-toast-' + level;
        toast.dataset.level = level;
        // Warnings and errors are announced straight away by screen readers.
        toast.setAttribute('role', level === 'warning' || level === 'danger' ? 'alert' : 'status');

        var icon = document.createElement('i');
        icon.className = 'fas ' + ICONS[level] + ' app-toast-icon';
        icon.setAttribute('aria-hidden', 'true');

        var body = document.createElement('div');
        body.className = 'app-toast-body';
        var title = document.createElement('strong');
        title.className = 'app-toast-title';
        title.textContent = TITLES[level];
        var message = document.createElement('div');
        message.textContent = text;  // text, never HTML
        body.appendChild(title);
        body.appendChild(message);

        var closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'app-toast-close';
        closeButton.setAttribute('aria-label', 'Dismiss this message');
        closeButton.innerHTML = '&times;';

        var timer = document.createElement('div');
        timer.className = 'app-toast-timer';

        toast.appendChild(icon);
        toast.appendChild(body);
        toast.appendChild(closeButton);
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
