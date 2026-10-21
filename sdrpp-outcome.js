/** Pure helpers for the SDR++ controls: what a control response means, and how a pending proposal reads. */
(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.SDRPPOutcome = api;
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    function formatFrequency(hz) {
        const value = Number(hz);
        if (!Number.isFinite(value)) return '?';
        if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(6)} GHz`;
        if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(3)} MHz`;
        if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)} kHz`;
        return `${value.toFixed(0)} Hz`;
    }

    /**
     * Classify a control response (`/api/sdr/*` or a proposal decision).
     * `kind` is one of executed, pending_approval, rejected, denied, invalid, unauthorized, unavailable, error.
     * A refusal is never `ok`, whatever the HTTP status says.
     */
    function describeResponse(httpStatus, data) {
        const body = data && typeof data === 'object' ? data : {};
        const proposalId = body.proposal_id || null;
        const reason = body.message || body.reason || '';
        if (httpStatus >= 200 && httpStatus < 300 && body.status === 'ok') {
            return { ok: true, kind: body.outcome === 'denied' ? 'denied' : 'executed', message: '', proposalId };
        }
        if (httpStatus === 401) return { ok: false, kind: 'unauthorized', message: 'Sign in again to control the radio.', proposalId };
        if (body.outcome === 'pending_approval') {
            return { ok: false, kind: 'pending_approval', proposalId,
                     message: 'Waiting for a second operator to approve this command.' };
        }
        if (httpStatus === 400) return { ok: false, kind: 'invalid', message: reason || 'The radio rejected that request.', proposalId };
        if (httpStatus === 403) return { ok: false, kind: 'rejected', message: reason || 'You are not allowed to do that.', proposalId };
        if (httpStatus === 409) return { ok: false, kind: 'rejected', message: reason || 'The command was refused.', proposalId };
        if (httpStatus === 503) return { ok: false, kind: 'unavailable', message: reason || 'The radio control service is unavailable.', proposalId };
        return { ok: false, kind: 'error', message: reason || `HTTP ${httpStatus}`, proposalId };
    }

    function minutesLeft(expiresAt, nowSeconds) {
        const left = Number(expiresAt) - nowSeconds;
        return Number.isFinite(left) ? Math.max(0, Math.ceil(left / 60)) : null;
    }

    /** One line for the approver: who asked for what, and how long it stays open. */
    function describeProposal(item, nowSeconds) {
        const params = item.params || {};
        const who = (item.proposed_by && (item.proposed_by.callsign || item.proposed_by.operator_id)) || 'an operator';
        let what;
        if (item.tool === 'rf_tune') {
            what = `tune to ${formatFrequency(params.frequency_hz)}${params.mode ? ` ${params.mode}` : ''}`;
        } else if (item.tool === 'rf_capture_start') {
            what = 'start the capture';
        } else if (item.tool === 'rf_capture_stop') {
            what = 'stop the capture';
        } else if (item.tool === 'rf_stream_configure') {
            what = `change the stream (${Object.keys(params).map(k => `${k}=${params[k]}`).join(', ') || 'no settings'})`;
        } else {
            what = String(item.tool || 'run a command');
        }
        const left = minutesLeft(item.expires_at, nowSeconds);
        return `${who}: ${what}${left === null ? '' : ` · open ${left} min`}`;
    }

    return { describeResponse, describeProposal, formatFrequency };
}));
