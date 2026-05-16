/**
 * MAGI sound FX — ported from magi-system frontend/src/lib/sfx.tsx
 * NERV-style synthetic tones via Web Audio API (no audio files).
 */
(function () {
    const STORAGE_KEY = 'magi:sfx';

    let enabled = true;
    let ctx = null;

    try {
        const v = localStorage.getItem(STORAGE_KEY);
        if (v !== null) enabled = v === '1';
    } catch (_) { /* ignore */ }

    function playBlip(audioCtx, opts) {
        const {
            freq,
            durationMs,
            type = 'square',
            gain = 0.07,
            attackMs = 2,
            releaseMs = 40,
            detune = 0,
        } = opts;
        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const g = audioCtx.createGain();
        osc.type = type;
        osc.frequency.setValueAtTime(freq, now);
        if (detune) osc.detune.setValueAtTime(detune, now);
        g.gain.setValueAtTime(0, now);
        g.gain.linearRampToValueAtTime(gain, now + attackMs / 1000);
        g.gain.exponentialRampToValueAtTime(0.0001, now + durationMs / 1000);
        osc.connect(g).connect(audioCtx.destination);
        osc.start(now);
        osc.stop(now + (durationMs + releaseMs) / 1000);
    }

    function playNoise(audioCtx, durationMs, gain) {
        gain = gain === undefined ? 0.03 : gain;
        const bufferSize = Math.max(1, Math.floor(audioCtx.sampleRate * (durationMs / 1000)));
        const buffer = audioCtx.createBuffer(1, bufferSize, audioCtx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i += 1) {
            data[i] = (Math.random() * 2 - 1) * Math.exp(-3 * (i / bufferSize));
        }
        const src = audioCtx.createBufferSource();
        src.buffer = buffer;
        const gn = audioCtx.createGain();
        gn.gain.value = gain;
        src.connect(gn).connect(audioCtx.destination);
        src.start();
    }

    function ensureCtx() {
        if (ctx) return ctx;
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return null;
        try {
            ctx = new Ctor();
            return ctx;
        } catch (_) {
            return null;
        }
    }

    function play(kind) {
        if (!enabled) return;
        const audioCtx = ensureCtx();
        if (!audioCtx) return;
        if (audioCtx.state === 'suspended') {
            audioCtx.resume().catch(function () {});
        }

        switch (kind) {
            case 'click':
                playBlip(audioCtx, { freq: 1760, durationMs: 40, type: 'square', gain: 0.06 });
                playBlip(audioCtx, {
                    freq: 2640,
                    durationMs: 35,
                    type: 'square',
                    gain: 0.03,
                    detune: 5,
                });
                playNoise(audioCtx, 20, 0.015);
                break;
            case 'soft':
                playBlip(audioCtx, { freq: 880, durationMs: 55, type: 'triangle', gain: 0.05 });
                break;
            case 'confirm':
                playBlip(audioCtx, { freq: 880, durationMs: 60, type: 'square', gain: 0.06 });
                setTimeout(function () {
                    playBlip(audioCtx, { freq: 1320, durationMs: 90, type: 'square', gain: 0.06 });
                }, 70);
                break;
            case 'alert':
                playBlip(audioCtx, { freq: 880, durationMs: 120, type: 'sawtooth', gain: 0.07 });
                setTimeout(function () {
                    playBlip(audioCtx, { freq: 440, durationMs: 180, type: 'sawtooth', gain: 0.06 });
                }, 130);
                break;
            case 'boot': {
                const now = audioCtx.currentTime;
                const osc = audioCtx.createOscillator();
                const g = audioCtx.createGain();
                osc.type = 'sawtooth';
                osc.frequency.setValueAtTime(220, now);
                osc.frequency.exponentialRampToValueAtTime(880, now + 0.28);
                g.gain.setValueAtTime(0, now);
                g.gain.linearRampToValueAtTime(0.05, now + 0.05);
                g.gain.exponentialRampToValueAtTime(0.0001, now + 0.4);
                osc.connect(g).connect(audioCtx.destination);
                osc.start(now);
                osc.stop(now + 0.5);
                playNoise(audioCtx, 100, 0.02);
                break;
            }
            default:
                break;
        }
    }

    function setEnabled(v) {
        enabled = !!v;
        try {
            localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0');
        } catch (_) { /* ignore */ }
        updateToggleUi();
    }

    function updateToggleUi() {
        const btn = document.getElementById('sfx-toggle');
        if (!btn) return;
        btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        btn.title = enabled ? '音效：开' : '音效：关';
        const label = btn.querySelector('.sfx-toggle-label');
        if (label) label.textContent = enabled ? 'SFX ON' : 'SFX OFF';
        const pip = btn.querySelector('.magi-pip');
        if (pip) pip.classList.toggle('ok', enabled);
    }

    function resolveSidebarKind(el) {
        if (el.closest('.task-card-link')) return 'soft';
        if (el.closest('.history-alert, .history-link')) return 'soft';
        if (el.closest('[data-action="delete"]')) return 'alert';
        if (el.closest('[data-action="retry"]')) return 'alert';
        if (el.closest('button, .magi-btn')) return 'click';
        if (el.tagName === 'A') return 'soft';
        return 'click';
    }

    function initDelegate() {
        document.addEventListener('click', function (e) {
            if (e.target.closest('#sfx-toggle')) return;
            if (e.target.closest('.card-preview-details summary, .card-preview-details .card-preview-text')) {
                return;
            }

            const explicit = e.target.closest('[data-sfx]');
            if (explicit) {
                play(explicit.getAttribute('data-sfx'));
                return;
            }

            const sidebarHit = e.target.closest(
                '.home-sidebar a[href], .home-sidebar button:not([disabled]), ' +
                '.queue-page .task-list a[href], .queue-page .task-list button:not([disabled])'
            );
            if (sidebarHit) {
                play(resolveSidebarKind(sidebarHit));
            }
        }, true);
    }

    function initToggle() {
        const btn = document.getElementById('sfx-toggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            const next = !enabled;
            setEnabled(next);
            if (next) play('boot');
        });
        updateToggleUi();
    }

    function initBoot() {
        if (document.querySelector('.home-layout')) {
            setTimeout(function () { play('boot'); }, 200);
        }
    }

    window.MagiSfx = {
        get enabled() { return enabled; },
        setEnabled: setEnabled,
        play: play,
        click: function () { play('click'); },
        soft: function () { play('soft'); },
        confirm: function () { play('confirm'); },
        alert: function () { play('alert'); },
        boot: function () { play('boot'); },
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initDelegate();
            initToggle();
            initBoot();
        });
    } else {
        initDelegate();
        initToggle();
        initBoot();
    }
})();
