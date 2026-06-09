/** 首页：SSE、搜索、任务卡片操作 */
(function () {
    const UI = window.UI_STRINGS || {};

    if (!document.querySelector('.home-layout')) return;

    initDownloaderPicker();
    initUrlFormSubmit();
    initCookiePanel();

    function initDownloaderPicker() {
        document.querySelectorAll('.dl-picker').forEach(function (picker) {
            const field = picker.closest('.dl-field');
            const input = field?.querySelector('input[name="downloader"]');
            const summary = picker.querySelector('.dl-picker-trigger');
            if (!input || !summary) return;

            picker.querySelectorAll('.dl-picker-option').forEach(function (btn) {
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    input.value = btn.getAttribute('data-value') || 'auto';
                    summary.textContent = btn.textContent.trim();
                    picker.open = false;
                    picker.querySelectorAll('.dl-picker-option').forEach(function (b) {
                        b.classList.toggle('is-selected', b === btn);
                    });
                });
            });
        });

        document.addEventListener('click', function (e) {
            if (e.target.closest('.dl-picker')) return;
            document.querySelectorAll('.dl-picker[open]').forEach(function (p) {
                p.open = false;
            });
        });
    }

    // 事件委托：卡片按钮
    document.addEventListener('click', async function (e) {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const action = btn.getAttribute('data-action');
        const jobId = btn.getAttribute('data-job-id');
        if (!jobId) return;
        e.preventDefault();
        if (action === 'cancel') await cancelJob(jobId);
        else if (action === 'delete') await deleteJob(jobId);
        else if (action === 'archive') await archiveJob(jobId);
        else if (action === 'unarchive') await unarchiveJob(jobId);
        else if (action === 'retry') await retryJob(jobId);
    });

    connectActiveJobsStream();
    if (document.getElementById('failed-alert')) {
        refreshFailedAlert();
    }

    function connectActiveJobsStream() {
        if (window._activeJobsSource) {
            window._activeJobsSource.close();
        }
        const evtSource = new EventSource('/api/active-jobs/stream');
        window._activeJobsSource = evtSource;

        evtSource.addEventListener('init', function (e) {
            const data = JSON.parse(e.data);
            data.jobs.forEach(function (j) { ensureActiveCard(j.id); });
            checkEmptyState();
        });

        evtSource.addEventListener('job_created', function (e) {
            const d = JSON.parse(e.data);
            ensureActiveCard(d.job_id);
            checkEmptyState();
        });

        evtSource.addEventListener('progress', function (e) {
            updateActiveCard(JSON.parse(e.data));
        });

        evtSource.addEventListener('completed', function (e) {
            const d = JSON.parse(e.data);
            removeCard(d.job_id);
            prependListCard(d.job_id);
            checkEmptyState();
            if (window.MagiSfx) window.MagiSfx.play('confirm');
        });

        evtSource.addEventListener('failed', function (e) {
            removeCard(JSON.parse(e.data).job_id);
            refreshFailedAlert();
            checkEmptyState();
            if (window.MagiSfx) window.MagiSfx.play('alert');
        });

        evtSource.addEventListener('cancelled', function (e) {
            removeCard(JSON.parse(e.data).job_id);
            checkEmptyState();
            showToast(UI.toastCancelled || '任务已取消', 'info');
        });

        evtSource.onerror = function () {
            evtSource.close();
            window._activeJobsSource = null;
            setTimeout(connectActiveJobsStream, 3000);
        };
    }

    function initUrlFormSubmit() {
        const form = document.getElementById('url-form');
        if (!form) return;
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            const btn = document.getElementById('btn-submit');
            const urlInput = document.getElementById('url-input');
            if (!btn || !urlInput?.value.trim()) return;

            const prevLabel = btn.textContent;
            btn.textContent = UI.btnSubmitting || '提交中…';
            btn.disabled = true;
            btn.classList.add('is-loading');

            try {
                const resp = await fetch('/api/jobs', {
                    method: 'POST',
                    body: new FormData(form),
                    headers: { Accept: 'application/json' },
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(function () { return {}; });
                    throw new Error(err.detail || resp.statusText || '提交失败');
                }
                const data = await resp.json();
                form.reset();
                resetDownloaderPicker();
                for (const job of data.jobs || []) {
                    await ensureActiveCard(job.id);
                }
                checkEmptyState();
                const n = (data.jobs || []).length;
                if (n > 0) {
                    showToast(
                        n === 1 ? '任务已提交，正在同步…' : '已提交 ' + n + ' 个任务，正在同步…',
                        'info'
                    );
                }
            } catch (err) {
                showToast((UI.errorSubmitFailed || '提交失败') + ': ' + err.message, 'error');
            } finally {
                btn.textContent = prevLabel;
                btn.disabled = false;
                btn.classList.remove('is-loading');
            }
        });
    }

    function resetDownloaderPicker() {
        const input = document.getElementById('downloader-input');
        const summary = document.getElementById('downloader-summary');
        if (input) input.value = 'auto';
        if (summary) summary.textContent = '自动选择';
        document.querySelectorAll('.dl-picker-option').forEach(function (btn) {
            btn.classList.toggle('is-selected', btn.getAttribute('data-value') === 'auto');
        });
    }

    window.doSearch = function (basePath) {
        const q = document.getElementById('search-input')?.value.trim() || '';
        const platform = document.getElementById('platform-filter')?.value || '';
        const path = basePath || '/';
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        if (platform) params.set('platform', platform);
        const qs = params.toString();
        window.location.href = qs ? path + '?' + qs : path;
    };

    window.doSemanticSearch = async function () {
        const q = document.getElementById('search-input')?.value.trim() || '';
        const panel = document.getElementById('semantic-results');
        if (!panel) return;
        const esc = function (s) {
            return String(s == null ? '' : s)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        };
        if (!q) { panel.hidden = true; panel.innerHTML = ''; return; }
        panel.hidden = false;
        panel.innerHTML = '<div class="semantic-empty">语义检索中…</div>';
        try {
            const resp = await fetch('/api/search/semantic?q=' + encodeURIComponent(q) + '&k=8');
            const data = await resp.json();
            if (!data.enabled) {
                panel.innerHTML = '<div class="semantic-empty">' + esc(data.message || '语义检索未启用') + '</div>';
                return;
            }
            if (!data.hits || !data.hits.length) {
                panel.innerHTML = '<div class="semantic-empty">没有找到语义相关的内容</div>';
                return;
            }
            const items = data.hits.map(function (h) {
                const dist = (typeof h.distance === 'number') ? h.distance.toFixed(3) : '';
                return '<a class="semantic-hit" href="/jobs/' + encodeURIComponent(h.job_id) + '">'
                    + '<span class="semantic-hit-title">' + esc(h.title || h.job_id) + '</span>'
                    + '<span class="semantic-hit-excerpt">' + esc(h.excerpt) + '</span>'
                    + '<span class="semantic-hit-dist">距离 ' + dist + '</span>'
                    + '</a>';
            }).join('');
            panel.innerHTML = '<div class="semantic-head">语义检索结果</div>' + items;
        } catch (err) {
            panel.innerHTML = '<div class="semantic-empty">检索失败：' + esc(err) + '</div>';
        }
    };

    document.getElementById('search-input')?.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') doSearch(window.location.pathname);
    });

    document.getElementById('platform-filter')?.addEventListener('change', function () {
        doSearch('/');
    });

    document.getElementById('btn-upload-local')?.addEventListener('click', async function () {
        const input = document.getElementById('local-file-input');
        const status = document.getElementById('upload-status');
        const file = input && input.files ? input.files[0] : null;
        if (!file) { if (status) status.textContent = '请先选择文件'; return; }
        const btn = this;
        btn.disabled = true;
        if (status) status.textContent = '上传中…';
        try {
            const fd = new FormData();
            fd.append('file', file);
            const resp = await fetch('/api/jobs/upload', {
                method: 'POST', body: fd, headers: { 'Accept': 'application/json' },
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            await resp.json();
            if (status) status.textContent = '已创建任务，正在处理…';
            window.location.href = '/';
        } catch (err) {
            if (status) status.textContent = '上传失败：' + err;
            btn.disabled = false;
        }
    });

    function formatTaskSeq(n) {
        return n < 10 ? '0' + n : String(n);
    }

    function renumberJobList() {
        const list = document.getElementById('job-list');
        if (!list) return;
        const start = parseInt(list.dataset.seqStart || '0', 10);
        list.querySelectorAll(':scope > .task-card').forEach(function (card, i) {
            const num = start + i + 1;
            let el = card.querySelector('.task-seq');
            const row = card.querySelector('.task-card-title-row');
            if (!el && row) {
                el = document.createElement('span');
                el.className = 'task-seq';
                const title = row.querySelector('.task-title');
                if (title) row.insertBefore(el, title);
            }
            if (el) {
                el.textContent = formatTaskSeq(num);
                el.setAttribute('aria-label', '序号 ' + num);
            }
        });
    }

    async function fetchCardHtml(jobId, variant) {
        const resp = await fetch('/api/jobs/' + jobId + '/card?variant=' + variant);
        if (!resp.ok) throw new Error('card fetch failed');
        return resp.text();
    }

    async function ensureActiveCard(jobId) {
        const section = document.getElementById('active-section');
        const list = document.getElementById('active-tasks');
        if (!section || !list) return;
        if (list.querySelector('.task-card[data-job-id="' + jobId + '"]')) return;
        section.hidden = false;
        const html = await fetchCardHtml(jobId, 'active');
        const wrap = document.createElement('div');
        wrap.innerHTML = html.trim();
        const card = wrap.firstElementChild;
        if (card) list.appendChild(card);
    }

    function updateActiveCard(data) {
        const card = document.querySelector('#active-tasks .task-card[data-job-id="' + data.job_id + '"]');
        if (!card) {
            ensureActiveCard(data.job_id).then(function () { updateActiveCard(data); });
            return;
        }
        const fill = card.querySelector('.progress-fill');
        if (fill) fill.style.width = data.progress_pct + '%';
        const badge = card.querySelector('.status-badge');
        if (badge) {
            badge.textContent = data.status_label || data.status;
            badge.className = 'status-badge status-' + data.status;
        }
        const pctEl = card.querySelector('.progress-pct');
        if (pctEl) pctEl.textContent = data.progress_pct + '%';
        const stageEl = card.querySelector('.stage-label');
        if (stageEl) stageEl.textContent = data.stage || '';
        const titleEl = card.querySelector('.task-title');
        if (titleEl && data.title) titleEl.textContent = data.title;
    }

    function removeCard(jobId) {
        const hadListCard = !!document.querySelector('#job-list .task-card[data-job-id="' + jobId + '"]');
        document.querySelectorAll('.task-card[data-job-id="' + jobId + '"]').forEach(function (c) { c.remove(); });
        if (hadListCard) renumberJobList();
    }

    async function prependListCard(jobId) {
        const list = document.getElementById('job-list');
        const section = document.getElementById('job-list-section');
        if (!list || !section) return;
        if (list.querySelector('.task-card[data-job-id="' + jobId + '"]')) return;
        try {
            const html = await fetchCardHtml(jobId, 'list');
            const wrap = document.createElement('div');
            wrap.innerHTML = html.trim();
            const card = wrap.firstElementChild;
            if (card) {
                section.hidden = false;
                list.insertBefore(card, list.firstChild);
                document.getElementById('empty-state').hidden = true;
                renumberJobList();
            }
        } catch (err) {
            console.error('prependListCard', err);
        }
    }

    async function archiveJob(jobId) {
        const resp = await fetch('/api/jobs/' + jobId + '/archive', { method: 'POST' });
        if (resp.ok) { removeCard(jobId); checkEmptyState(); }
    }

    async function unarchiveJob(jobId) {
        const resp = await fetch('/api/jobs/' + jobId + '/unarchive', { method: 'POST' });
        if (resp.ok) { removeCard(jobId); checkEmptyState(); }
    }

    async function deleteJob(jobId) {
        const ok = await confirmDialog(UI.confirmDelete || '确定删除该任务及全部文件？');
        if (!ok) return;
        const resp = await fetch('/api/jobs/' + jobId, { method: 'DELETE' });
        if (resp.ok) { removeCard(jobId); checkEmptyState(); }
    }

    async function retryJob(jobId) {
        const card = document.querySelector('.task-card[data-job-id="' + jobId + '"]');
        if (card) card.classList.add('is-loading');
        try {
            const resp = await fetch('/api/jobs/' + jobId + '/retry', { method: 'POST' });
            if (resp.ok) {
                removeCard(jobId);
                await ensureActiveCard(jobId);
                checkEmptyState();
            } else {
                if (card) card.classList.remove('is-loading');
                const err = await resp.json();
                showToast((UI.errorRetryFailed || '重试失败') + ': ' + (err.detail || ''), 'error');
            }
        } catch (e) {
            if (card) card.classList.remove('is-loading');
            showToast((UI.errorRetryRequest || '重试请求失败') + ': ' + e.message, 'error');
        }
    }

    async function cancelJob(jobId) {
        const ok = await confirmDialog(UI.confirmCancel || '确定取消此任务？');
        if (!ok) return;
        await fetch('/api/jobs/' + jobId + '/cancel', { method: 'POST' });
    }

    function formatFailedAlertText(failedCount, stuckCount, staleMinutes) {
        const parts = [];
        if (failedCount > 0) parts.push(failedCount + ' 条失败');
        if (stuckCount > 0) {
            parts.push(stuckCount + ' 条进行中卡住（超过 ' + staleMinutes + ' 分钟）');
        }
        return parts.join('，') + ' · 查看详情';
    }

    function renderFailedAlert(failedCount, stuckCount, staleMinutes) {
        const sidebar = document.querySelector('.home-sidebar');
        if (!sidebar) return;

        let alert = document.getElementById('failed-alert');
        if (failedCount <= 0 && stuckCount <= 0) {
            if (alert) alert.remove();
            return;
        }

        if (!alert) {
            alert = document.createElement('a');
            alert.id = 'failed-alert';
            alert.href = '/jobs/failed';
            alert.innerHTML =
                '<span class="history-alert-dot" aria-hidden="true"></span>' +
                '<span class="history-alert-text" id="failed-alert-text"></span>' +
                '<span class="history-alert-arrow" aria-hidden="true">→</span>';
            const toolbar = sidebar.querySelector('.home-toolbar');
            if (toolbar) {
                sidebar.insertBefore(alert, toolbar);
            } else {
                sidebar.prepend(alert);
            }
        }

        alert.classList.toggle('history-alert-warn', stuckCount > 0);
        const textEl = document.getElementById('failed-alert-text');
        if (textEl) {
            textEl.textContent = formatFailedAlertText(failedCount, stuckCount, staleMinutes);
        }
    }

    async function refreshFailedAlert() {
        try {
            const resp = await fetch('/api/jobs/alert-summary');
            if (!resp.ok) return;
            const data = await resp.json();
            renderFailedAlert(
                data.failed_count || 0,
                data.stuck_count || 0,
                data.stale_job_minutes || 20
            );
        } catch (err) {
            console.warn('refreshFailedAlert failed', err);
        }
    }

    function checkEmptyState() {
        const activeCards = document.querySelectorAll('#active-tasks > .task-card');
        const listCards = document.querySelectorAll('#job-list > .task-card');
        const empty = document.getElementById('empty-state');
        const filterEmpty = document.getElementById('filter-empty');
        const activeSection = document.getElementById('active-section');
        const listSection = document.getElementById('job-list-section');
        const hasList = listCards.length > 0;
        const hasActive = activeCards.length > 0;
        const hasFilter = listSection?.dataset.hasFilter === '1';

        if (empty) empty.hidden = hasActive || hasList || hasFilter;
        if (filterEmpty) filterEmpty.hidden = hasList;
        if (activeSection) activeSection.hidden = !hasActive;
        // 列表区由服务端按筛选渲染，勿因 SSE 误隐藏
        if (listSection && hasList) listSection.hidden = false;
    }

    function initCookiePanel() {
        const panel = document.getElementById('cookie-panel');
        if (!panel) return;
        refreshCookieStatus();
        panel.addEventListener('click', async function (e) {
            const btn = e.target.closest('[data-cookie-action]');
            if (!btn) return;
            e.preventDefault();
            const action = btn.getAttribute('data-cookie-action');
            if (action === 'sync-xhs') await syncXhsCookie(false);
            else if (action === 'paste-xhs') await syncXhsCookie(true);
        });
    }

    async function refreshCookieStatus() {
        const list = document.getElementById('cookie-status-list');
        if (!list) return;
        try {
            const resp = await fetch('/api/cookies/status');
            if (!resp.ok) return;
            const data = await resp.json();
            list.innerHTML = (data.platforms || []).map(renderCookieStatusItem).join('');
            const syncBtn = document.getElementById('btn-sync-xhs-cookie');
            if (syncBtn && data.in_docker) {
                syncBtn.title = 'Docker 内建议使用「粘贴 Cookie」，或在宿主机执行 ./scripts/sync-xhs-cookie.sh';
            }
        } catch (err) {
            console.error('refreshCookieStatus', err);
        }
    }

    function renderCookieStatusItem(p) {
        const pip = p.configured ? 'ok' : 'warn';
        const time = p.updated_at ? ' · ' + p.updated_at : '';
        return (
            '<li class="cookie-status-item">' +
            '<span class="magi-pip ' + pip + '" aria-hidden="true"></span>' +
            '<span class="cookie-status-body">' +
            '<span class="cookie-status-name">' + escapeHtml(p.name) + '</span>' +
            '<span class="cookie-status-meta">' + escapeHtml(p.source_label) + escapeHtml(time) + '</span>' +
            '</span></li>'
        );
    }

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    async function syncXhsCookie(pasteMode) {
        const browser = document.getElementById('cookie-browser')?.value || 'chrome';
        const syncBtn = document.getElementById('btn-sync-xhs-cookie');
        const body = { browser: browser };

        if (pasteMode) {
            const cookie = window.prompt(
                '粘贴小红书 Cookie\n（浏览器 F12 → Network → 任意 xiaohongshu.com 请求 → Request Headers → Cookie）'
            );
            if (!cookie || !cookie.trim()) return;
            body.cookie = cookie.trim();
        }

        const prev = syncBtn?.textContent;
        if (syncBtn) {
            syncBtn.disabled = true;
            syncBtn.classList.add('is-loading');
            syncBtn.textContent = pasteMode ? '保存中…' : '同步中…';
        }

        try {
            const resp = await fetch('/api/cookies/sync/xiaohongshu', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(function () { return {}; });
            if (!resp.ok) {
                let detail = data.detail;
                if (typeof detail === 'object' && detail !== null) {
                    detail = detail.msg || JSON.stringify(detail);
                }
                if (resp.status === 404) {
                    detail =
                        '服务未加载最新接口，请执行 docker compose restart 后重试';
                }
                throw new Error(detail || resp.statusText || '同步失败');
            }
            showToast(data.message || '小红书 Cookie 已更新', 'info');
            refreshCookieStatus();
        } catch (err) {
            showToast((UI.errorCookieSync || 'Cookie 同步失败') + ': ' + err.message, 'error');
        } finally {
            if (syncBtn) {
                syncBtn.disabled = false;
                syncBtn.classList.remove('is-loading');
                syncBtn.textContent = prev || '同步小红书 Cookie';
            }
        }
    }
})();
