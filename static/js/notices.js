// Messages from the card office (dashboard): opening the stack shows every
// message and marks them read, which clears the "new" counts on the panel
// and on the bell in the menu bar. Arriving from the bell (#messages) opens it.
(function () {
    var panel = document.getElementById('messages');
    var list = document.getElementById('all-notices');
    if (!panel || !list || !window.bootstrap) {
        return;
    }
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
            document.querySelectorAll('.notice-dot').forEach(function (dot) { dot.remove(); });
        }).catch(function () { marked = false; });
    }

    list.addEventListener('show.bs.collapse', markRead);

    if (window.location.hash === '#messages') {
        bootstrap.Collapse.getOrCreateInstance(list).show();
    }
})();
