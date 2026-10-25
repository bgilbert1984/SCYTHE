/** Browser adapter for the authenticated SDR++ edge bridge. */
(function () {
    'use strict';

    const state = {
        abortController: null,
        streaming: false,
        lastSequence: 0,
        lastFrameAt: 0,
        reconnectTimer: null,
        canvas: null,
        context: null,
    };

    function apiFetch(path, options) {
        if (window.ScytheTransport && typeof window.ScytheTransport.fetch === 'function') {
            return window.ScytheTransport.fetch(path, options || {});
        }
        return fetch(path, options || {});
    }

    function setStatus(kind, text) {
        const dot = document.getElementById('sdrpp-live-dot');
        const label = document.getElementById('sdrpp-live-status');
        if (label) label.textContent = text;
        if (dot) {
            const colors = { live: '#31e981', waiting: '#ffbd2e', error: '#ff4d67', off: '#7b8494' };
            dot.style.background = colors[kind] || colors.off;
            dot.style.boxShadow = kind === 'live' ? `0 0 8px ${colors.live}` : 'none';
        }
    }

    function notify(title, message, type) {
        if (typeof window.showNotification === 'function') {
            window.showNotification(title, message, type || 'info');
        }
        if (typeof window.addConsoleMessage === 'function') {
            window.addConsoleMessage(`${title}: ${message}`, type === 'error' ? 'alert' : 'response');
        }
    }

    // A control response is judged by what the lifecycle decided, not by the transport status alone.
    async function readOutcome(response) {
        let data = null;
        try { data = await response.json(); } catch (error) { data = null; }
        return { ...window.SDRPPOutcome.describeResponse(response.status, data), data };
    }

    // Commands waiting for a second operator (ADR 0006), and the controls to approve or deny them.
    const approvals = { timer: null, items: [], mayApprove: false };

    function approvalsPanel() { return document.getElementById('sdrpp-approvals'); }

    function renderApprovals() {
        const panel = approvalsPanel();
        if (!panel) return;
        const button = document.getElementById('sdrpp-approvals-btn');
        const count = approvals.items.length;
        if (button) button.textContent = count ? `Approvals (${count})` : 'Approvals';
        panel.textContent = '';
        if (!count) {
            const empty = document.createElement('div');
            empty.textContent = 'No commands are waiting for approval.';
            panel.appendChild(empty);
            return;
        }
        const now = Date.now() / 1000;
        approvals.items.forEach(item => {
            const row = document.createElement('div');
            row.className = 'sdrpp-approval-row';
            const text = document.createElement('span');
            text.textContent = window.SDRPPOutcome.describeProposal(item, now);
            row.appendChild(text);
            if (item.own) {
                const note = document.createElement('em');
                note.textContent = ' (yours: another operator must approve)';
                row.appendChild(note);
            } else if (approvals.mayApprove !== true) {
                const note = document.createElement('em');
                note.textContent = ' (you are not authorized to approve commands)';
                row.appendChild(note);
            } else {
                [['approve', 'Approve'], ['deny', 'Deny']].forEach(([decision, label]) => {
                    const choice = document.createElement('button');
                    choice.className = 'spec-control';
                    choice.textContent = label;
                    choice.addEventListener('click', () => decide(item.proposal_id, decision));
                    row.appendChild(choice);
                });
            }
            panel.appendChild(row);
        });
    }

    async function refreshApprovals() {
        if (!approvalsPanel()) return;
        try {
            const response = await apiFetch('/api/mcp/proposals/pending');
            if (!response.ok) return;   // not signed in as an operator, or approvals unavailable: show nothing
            const data = await response.json();
            approvals.items = Array.isArray(data.proposals) ? data.proposals : [];
            approvals.mayApprove = data.caller_may_approve === true;   // fail closed: only an explicit "yes" shows the buttons
            renderApprovals();
        } catch (error) { /* keep the last list */ }
    }

    async function decide(proposalId, decision) {
        try {
            const response = await apiFetch(`/api/mcp/proposals/${encodeURIComponent(proposalId)}/decision`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ decision }),
            });
            const outcome = await readOutcome(response);
            if (outcome.ok) {
                notify('SDR++ approval', decision === 'approve' ? 'Approved and executed.' : 'Denied.', 'info');
            } else {
                notify('SDR++ approval', outcome.message, 'error');
            }
        } finally {
            refreshApprovals();
        }
    }

    function resizeCanvas() {
        const canvas = state.canvas || document.getElementById('spectrogramCanvas');
        if (!canvas) return null;
        const rect = canvas.getBoundingClientRect();
        const width = Math.max(320, Math.floor(rect.width || canvas.parentElement?.clientWidth || 900));
        const height = Math.max(160, Math.floor(rect.height || canvas.parentElement?.clientHeight || 320));
        if (canvas.width !== width || canvas.height !== height) {
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#040712';
            ctx.fillRect(0, 0, width, height);
        }
        state.canvas = canvas;
        state.context = canvas.getContext('2d');
        return canvas;
    }

    function heatColor(dbfs) {
        const t = Math.max(0, Math.min(1, (Number(dbfs) + 120) / 100));
        if (t < 0.25) return [2, Math.round(20 + t * 180), Math.round(60 + t * 600)];
        if (t < 0.55) return [Math.round((t - 0.25) * 500), Math.round(85 + t * 260), 225];
        if (t < 0.8) return [Math.round(80 + t * 210), Math.round(250 - (t - 0.55) * 280), 80];
        return [255, Math.round(180 + (t - 0.8) * 375), Math.round((t - 0.8) * 800)];
    }

    function formatFrequency(hz) {
        const value = Number(hz);
        if (!Number.isFinite(value)) return '—';
        if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(6)} GHz`;
        if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(3)} MHz`;
        if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)} kHz`;
        return `${value.toFixed(0)} Hz`;
    }

    function updateFrequencyScale(frame) {
        const scale = document.getElementById('frequencyScale');
        if (scale) {
            const min = Number(frame.min_frequency_hz);
            const max = Number(frame.max_frequency_hz);
            const labels = scale.querySelectorAll('span');
            labels.forEach((label, index) => {
                const ratio = labels.length <= 1 ? 0 : index / (labels.length - 1);
                label.textContent = formatFrequency(min + (max - min) * ratio);
            });
        }
        const current = document.getElementById('currentFrequency');
        if (current) {
            current.textContent = `${formatFrequency(frame.center_frequency_hz)} · peak ${formatFrequency(frame.peak_frequency_hz)} · ${Number(frame.peak_dbfs).toFixed(1)} dBFS`;
        }
    }

    function drawFrame(frame) {
        const canvas = resizeCanvas();
        const ctx = state.context;
        const bins = Array.isArray(frame.bins_dbfs) ? frame.bins_dbfs : [];
        if (!canvas || !ctx || bins.length === 0) return;

        // Scroll the existing waterfall upward and paint one new FFT row.
        ctx.drawImage(canvas, 0, 1, canvas.width, canvas.height - 1, 0, 0, canvas.width, canvas.height - 1);
        const row = ctx.createImageData(canvas.width, 1);
        for (let x = 0; x < canvas.width; x += 1) {
            const index = Math.min(bins.length - 1, Math.floor(x * bins.length / canvas.width));
            const [r, g, b] = heatColor(bins[index]);
            const offset = x * 4;
            row.data[offset] = r;
            row.data[offset + 1] = g;
            row.data[offset + 2] = b;
            row.data[offset + 3] = 255;
        }
        ctx.putImageData(row, 0, canvas.height - 1);
        state.lastSequence = Number(frame.sequence) || state.lastSequence;
        state.lastFrameAt = Date.now();
        updateFrequencyScale(frame);
        setStatus('live', `LIVE · ${Number(frame.sample_rate_hz / 1e6).toFixed(2)} MS/s · frame ${state.lastSequence}`);
    }

    async function consumeNDJSON(response, signal) {
        if (!response.body || typeof response.body.getReader !== 'function') {
            throw new Error('This browser does not support streaming fetch responses');
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = '';
        while (!signal.aborted) {
            const { value, done } = await reader.read();
            if (done) break;
            pending += decoder.decode(value, { stream: true });
            let newline;
            while ((newline = pending.indexOf('\n')) !== -1) {
                const line = pending.slice(0, newline).trim();
                pending = pending.slice(newline + 1);
                if (!line) continue;
                const event = JSON.parse(line);
                if (event.type === 'spectrum' && event.frame) drawFrame(event.frame);
                if (event.type === 'heartbeat' && Date.now() - state.lastFrameAt > 5000) {
                    setStatus('waiting', 'CONNECTED · waiting for IQ samples');
                }
                if (event.type === 'bridge_stopped') setStatus('off', 'STOPPED');
            }
        }
    }

    async function openStream() {
        if (state.abortController) state.abortController.abort();
        state.abortController = new AbortController();
        const signal = state.abortController.signal;
        const response = await apiFetch(`/api/sdr/spectrum/stream?after=${state.lastSequence}`, {
            headers: { Accept: 'application/x-ndjson' },
            signal,
        });
        if (!response.ok) throw new Error(`Spectrum stream HTTP ${response.status}`);
        state.streaming = true;
        try {
            await consumeNDJSON(response, signal);
        } finally {
            state.streaming = false;
        }
    }

    async function startSpectrum(container) {
        if (container) container.style.display = 'block';
        setStatus('waiting', 'CONNECTING TO SDR++');
        try {
            const statusResponse = await apiFetch('/api/sdr/status');
            if (statusResponse.status === 404 || statusResponse.status === 503) return false;
            if (!statusResponse.ok) throw new Error(`SDR status HTTP ${statusResponse.status}`);
            const status = await statusResponse.json();
            if (!status.running) {
                const startResponse = await apiFetch('/api/sdr/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}',
                });
                const outcome = await readOutcome(startResponse);
                if (outcome.kind === 'pending_approval') {
                    setStatus('waiting', 'AWAITING SECOND-OPERATOR APPROVAL');
                    notify('SDR++ bridge', outcome.message, 'warning');
                    refreshApprovals();
                    return true;   // handled: nothing was started, so do not open the stream
                }
                if (!outcome.ok) throw new Error(outcome.message || `SDR start HTTP ${startResponse.status}`);
            }
            if (!state.streaming) {
                openStream().catch(error => {
                    if (error.name === 'AbortError') return;
                    setStatus('error', `STREAM ERROR · ${error.message}`);
                    notify('SDR++ stream', error.message, 'error');
                });
            }
            notify('SDR++ bridge', 'Live spectrum requested; waiting for IQ Exporter samples.', 'info');
            return true;
        } catch (error) {
            setStatus('error', `OFFLINE · ${error.message}`);
            notify('SDR++ bridge', error.message, 'error');
            return true;
        }
    }

    async function stopSpectrum() {
        if (state.abortController) state.abortController.abort();
        state.abortController = null;
        state.streaming = false;
        try {
            const response = await apiFetch('/api/sdr/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
            const outcome = await readOutcome(response);
            if (!outcome.ok) notify('SDR++ stop', outcome.message, 'error');
        } finally {
            setStatus('off', 'STOPPED');
        }
    }

    async function tune(frequencyHz, mode, bandwidthHz) {
        const response = await apiFetch('/api/sdr/tune', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frequency_hz: Number(frequencyHz),
                mode: mode || undefined,
                bandwidth_hz: Number(bandwidthHz) || 0,
            }),
        });
        const outcome = await readOutcome(response);
        if (outcome.kind === 'pending_approval') refreshApprovals();
        if (!outcome.ok) throw new Error(outcome.message || `Tune HTTP ${response.status}`);
        notify('SDR++ tuned', formatFrequency(frequencyHz), 'info');
        return outcome.data;
    }

    function parseFrequency(text) {
        const match = String(text || '').trim().match(/^([0-9]+(?:\.[0-9]+)?)\s*([kmg])?(?:hz)?$/i);
        if (!match) return NaN;
        const scales = { k: 1e3, m: 1e6, g: 1e9 };
        return Number(match[1]) * (scales[(match[2] || '').toLowerCase()] || 1);
    }

    function initializeControls() {
        const disconnect = document.getElementById('sdrpp-disconnect-btn');
        if (disconnect) disconnect.addEventListener('click', stopSpectrum);
        const tuneButton = document.getElementById('sdrpp-tune-btn');
        if (tuneButton) {
            tuneButton.addEventListener('click', async () => {
                const input = document.getElementById('sdrpp-frequency-input');
                const frequency = parseFrequency(input?.value);
                if (!Number.isFinite(frequency) || frequency <= 0) {
                    notify('SDR++ tune', 'Enter a frequency such as 145.350M.', 'error');
                    return;
                }
                try {
                    await tune(frequency);
                } catch (error) {
                    notify('SDR++ tune failed', error.message, 'error');
                }
            });
        }
        const approvalsButton = document.getElementById('sdrpp-approvals-btn');
        if (approvalsButton) {
            approvalsButton.addEventListener('click', () => {
                const panel = approvalsPanel();
                if (panel) panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
                refreshApprovals();
            });
            if (!approvals.timer) approvals.timer = setInterval(() => { if (!document.hidden) refreshApprovals(); }, 20000);
            refreshApprovals();
        }
        window.addEventListener('resize', resizeCanvas);
    }

    window.SDRPPBridge = {
        startSpectrum,
        stopSpectrum,
        tune,
        parseFrequency,
        refreshApprovals,
        drawFrame,
        state,
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeControls, { once: true });
    } else {
        initializeControls();
    }
})();
