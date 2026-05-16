/** 任务详情页 */
(function () {
    const jobId = window.JOB_ID;
    if (!jobId) return;

    const UI = window.UI_STRINGS || {};

    document.addEventListener('click', async function (e) {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        e.preventDefault();
        if (action === 'retry') await retryJob();
        else if (action === 'delete') await deleteJob();
        else if (action === 'archive') await archiveJob();
        else if (action === 'unarchive') await unarchiveJob();
        else if (action === 'cancel') await cancelJob();
    });

    async function retryJob() {
        const resp = await fetch('/api/jobs/' + jobId + '/retry', { method: 'POST' });
        if (resp.ok) {
            window.location.reload();
            return;
        }
        const err = await resp.json().catch(function () { return {}; });
        showToast((UI.errorRetryFailed || '重试失败') + ': ' + (err.detail || resp.statusText), 'error');
    }

    async function deleteJob() {
        const ok = await confirmDialog(UI.confirmDelete || '确定删除该任务及全部文件？');
        if (!ok) return;
        const resp = await fetch('/api/jobs/' + jobId, { method: 'DELETE' });
        if (resp.ok) window.location.href = '/';
    }

    async function archiveJob() {
        const resp = await fetch('/api/jobs/' + jobId + '/archive', { method: 'POST' });
        if (resp.ok) window.location.href = '/';
    }

    async function unarchiveJob() {
        const resp = await fetch('/api/jobs/' + jobId + '/unarchive', { method: 'POST' });
        if (resp.ok) window.location.href = '/';
    }

    async function cancelJob() {
        const ok = await confirmDialog(UI.confirmCancel || '确定取消此任务？');
        if (!ok) return;
        const resp = await fetch('/api/jobs/' + jobId + '/cancel', { method: 'POST' });
        if (resp.ok) {
            const ps = document.getElementById('progress-section');
            if (ps) ps.hidden = true;
            showToast(UI.toastCancelled || '任务已取消', 'info');
        }
    }

    async function refreshJobDynamic() {
        const resp = await fetch('/api/jobs/' + jobId + '/fragment');
        if (!resp.ok) return;
        const html = await resp.text();
        const el = document.getElementById('job-dynamic');
        if (el) el.innerHTML = html;
        const badge = document.querySelector('.job-hero-status');
        const jobResp = await fetch('/api/jobs/' + jobId);
        if (jobResp.ok && badge) {
            const job = await jobResp.json();
            badge.textContent = job.status_label || job.status;
            badge.className = 'job-hero-status status-' + job.status;
        }
    }

    if (window.JOB_IS_ACTIVE) {
        const evtSource = new EventSource('/api/jobs/' + jobId + '/stream');
        evtSource.addEventListener('progress', function (e) {
            const d = JSON.parse(e.data);
            const fill = document.getElementById('progress-fill');
            const pct = document.getElementById('progress-pct');
            const stage = document.getElementById('progress-stage');
            if (fill) fill.style.width = d.progress_pct + '%';
            if (pct) pct.textContent = d.progress_pct + '%';
            if (stage) stage.textContent = d.stage || '';
            const badge = document.querySelector('.job-hero-status');
            if (badge && d.status_label) {
                badge.textContent = d.status_label;
                badge.className = 'job-hero-status status-' + d.status;
            }
        });
        evtSource.addEventListener('completed', function () {
            evtSource.close();
            const ps = document.getElementById('progress-section');
            if (ps) ps.hidden = true;
            refreshJobDynamic();
        });
        evtSource.addEventListener('failed', function () {
            evtSource.close();
            const ps = document.getElementById('progress-section');
            if (ps) ps.hidden = true;
            refreshJobDynamic();
        });
        evtSource.addEventListener('cancelled', function () {
            evtSource.close();
            const ps = document.getElementById('progress-section');
            if (ps) ps.hidden = true;
        });
    }
})();
