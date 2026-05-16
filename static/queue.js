/** 列表页（失败 / 归档）：任务卡片操作 + 搜索，不启用首页 SSE */
(function () {
    const UI = window.UI_STRINGS || {};

    document.querySelectorAll('[data-nav-home]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.stopPropagation();
        });
    });

    document.addEventListener('click', async function (e) {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        const jobId = btn.getAttribute('data-job-id');
        if (!jobId) return;
        e.preventDefault();
        if (action === 'delete') await deleteJob(jobId);
        else if (action === 'archive') await archiveJob(jobId);
        else if (action === 'unarchive') await unarchiveJob(jobId);
        else if (action === 'retry') await retryJob(jobId);
    });

    window.doSearch = function (basePath) {
        const q = document.getElementById('search-input')?.value.trim() || '';
        const path = basePath || window.location.pathname;
        const params = new URLSearchParams(window.location.search);
        if (q) params.set('q', q);
        else params.delete('q');
        const qs = params.toString();
        window.location.href = qs ? path + '?' + qs : path;
    };

    document.getElementById('search-input')?.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') doSearch(window.location.pathname);
    });

    async function deleteJob(jobId) {
        const ok = await confirmDialog(UI.confirmDelete || '确定删除该任务及全部文件？');
        if (!ok) return;
        const resp = await fetch('/api/jobs/' + jobId, { method: 'DELETE' });
        if (resp.ok) {
            document.querySelectorAll('.task-card[data-job-id="' + jobId + '"]').forEach(function (c) {
                c.remove();
            });
        }
    }

    async function archiveJob(jobId) {
        const resp = await fetch('/api/jobs/' + jobId + '/archive', { method: 'POST' });
        if (resp.ok) {
            document.querySelectorAll('.task-card[data-job-id="' + jobId + '"]').forEach(function (c) {
                c.remove();
            });
        }
    }

    async function unarchiveJob(jobId) {
        const resp = await fetch('/api/jobs/' + jobId + '/unarchive', { method: 'POST' });
        if (resp.ok) {
            document.querySelectorAll('.task-card[data-job-id="' + jobId + '"]').forEach(function (c) {
                c.remove();
            });
        }
    }

    async function retryJob(jobId) {
        const card = document.querySelector('.task-card[data-job-id="' + jobId + '"]');
        if (card) card.classList.add('is-loading');
        try {
            const resp = await fetch('/api/jobs/' + jobId + '/retry', { method: 'POST' });
            if (resp.ok) {
                window.location.href = '/';
            } else {
                if (card) card.classList.remove('is-loading');
                const err = await resp.json().catch(function () { return {}; });
                showToast((UI.errorRetryFailed || '重试失败') + ': ' + (err.detail || ''), 'error');
            }
        } catch (e) {
            if (card) card.classList.remove('is-loading');
            showToast((UI.errorRetryRequest || '重试请求失败') + ': ' + e.message, 'error');
        }
    }
})();
