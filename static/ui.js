/** 轻量对话框与 Toast */
(function () {
    function ensureOverlay() {
        let el = document.getElementById('ui-overlay');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'ui-overlay';
        el.className = 'ui-overlay';
        el.hidden = true;
        el.innerHTML =
            '<div class="ui-dialog" role="dialog" aria-modal="true">' +
            '<p class="ui-dialog-message"></p>' +
            '<div class="ui-dialog-actions">' +
            '<button type="button" class="magi-btn ghost" data-ui-cancel>取消</button>' +
            '<button type="button" class="magi-btn danger" data-ui-ok>确定</button>' +
            '</div></div>';
        document.body.appendChild(el);
        return el;
    }

    function ensureToastHost() {
        let el = document.getElementById('ui-toast-host');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'ui-toast-host';
        el.className = 'ui-toast-host';
        document.body.appendChild(el);
        return el;
    }

    window.confirmDialog = function (message) {
        return new Promise(function (resolve) {
            const overlay = ensureOverlay();
            const msg = overlay.querySelector('.ui-dialog-message');
            const ok = overlay.querySelector('[data-ui-ok]');
            const cancel = overlay.querySelector('[data-ui-cancel]');
            msg.textContent = message;
            overlay.hidden = false;

            function cleanup(result) {
                overlay.hidden = true;
                ok.removeEventListener('click', onOk);
                cancel.removeEventListener('click', onCancel);
                overlay.removeEventListener('click', onBackdrop);
                resolve(result);
            }
            function onOk() { cleanup(true); }
            function onCancel() { cleanup(false); }
            function onBackdrop(e) {
                if (e.target === overlay) cleanup(false);
            }
            ok.addEventListener('click', onOk);
            cancel.addEventListener('click', onCancel);
            overlay.addEventListener('click', onBackdrop);
        });
    };

    window.showToast = function (message, type) {
        if (window.MagiSfx) {
            if (type === 'error') window.MagiSfx.play('alert');
            else window.MagiSfx.play('soft');
        }
        const host = ensureToastHost();
        const t = document.createElement('div');
        t.className = 'ui-toast' + (type ? ' ui-toast-' + type : '');
        t.textContent = message;
        host.appendChild(t);
        setTimeout(function () {
            t.classList.add('ui-toast-hide');
            setTimeout(function () { t.remove(); }, 300);
        }, 3200);
    };
})();
