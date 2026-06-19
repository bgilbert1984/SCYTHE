/**
 * SCYTHE Auth Helper (shared_auth.js)
 * 
 * Centralizes session persistence, validation, and API interaction
 * across all SCYTHE interfaces.
 */

const SCYTHE_AUTH = (function() {
    'use strict';

    const IS_BROKER = (window.SCYTHE_RUNTIME.runtimeRole === 'broker');

    const TOKEN_KEY = IS_BROKER
        ? 'scythe:orchestrator:brokerSession' 
        : `${window.SCYTHE_RUNTIME.storagePrefix}:operatorSession`;
    const COOKIE_NAME = IS_BROKER
        ? 'scythe:orchestrator:brokerSessionToken'
        : `${window.SCYTHE_RUNTIME.storagePrefix}:operatorSessionToken`;

    // Cached decoded JWT claims (if available) to avoid repeated decoding
    let currentClaims = null;

    // ── Shared API Base Resolution ──────────────────────────────────────────
    function getApiBase() {
        return window.API_BASE || window.SCYTHE_API_BASE || window.location.origin;
    }

    // ── Shared Cookie Helpers ───────────────────────────────────────────────
    function setCookie(name, value, days = 30) {
        const d = new Date();
        d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
        document.cookie = `${name}=${encodeURIComponent(value)}; path=/; expires=${d.toUTCString()}`;
    }

    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
            return decodeURIComponent(parts.pop().split(';').shift());
        }
        return null;
    }

    function clearCookie(name) {
        document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 UTC`;
    }

    // ── Session Persistence ──────────────────────────────────────────────────
    function persistSession(token) {
        console.log(`[Auth] Persisting session to ${TOKEN_KEY} (Role: ${IS_BROKER ? 'broker' : 'instance'})`);
        
        if (IS_BROKER) {
            sessionStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionToken: token }));
        } else {
            localStorage.setItem(TOKEN_KEY, JSON.stringify({ sessionToken: token }));
            setCookie(COOKIE_NAME, token);
        }
        
        ScytheTransport.setSessionToken(token);
    }

    function getInstanceIdFromPath() {
        try {
            const parts = window.location.pathname.split('/');
            const iIdx = parts.indexOf('i');
            if (iIdx !== -1 && parts.length > iIdx + 1) {
                return parts[iIdx + 1];
            }
        } catch (e) {
            console.warn('[Auth] Failed to parse instance id from path', e);
        }
        return null;
    }

    function restoreSession() {
        const storage = IS_BROKER ? sessionStorage : localStorage;
        let saved = storage.getItem(TOKEN_KEY);
        
        // Fallback for broker transition: try localStorage if sessionStorage is empty
        if (IS_BROKER && !saved) {
            saved = localStorage.getItem(TOKEN_KEY);
        }

        if (saved) {
            try {
                const data = JSON.parse(saved);
                const token = data.sessionToken || data.token; 
                if (token) {
                    if (!IS_BROKER) setCookie(COOKIE_NAME, token);
                    ScytheTransport.setSessionToken(token);
                    console.log(`[Auth] Restored session token from ${TOKEN_KEY}`);
                    return token;
                }
            } catch (e) {
                console.error('[Auth] Corrupt session data:', e);
            }
        }

        if (!IS_BROKER) {
            const cookieToken = getCookie(COOKIE_NAME);
            if (cookieToken) {
                persistSession(cookieToken);
                return cookieToken;
            }
        }
        
        console.log('[Auth] No session found');
        ScytheTransport.clearSessionToken();
        return null;
    }

    function logout() {
        if (IS_BROKER) {
            sessionStorage.removeItem(TOKEN_KEY);
        } else {
            localStorage.removeItem(TOKEN_KEY);
            clearCookie(COOKIE_NAME);
        }
        ScytheTransport.clearSessionToken();
        window.dispatchEvent(new Event('scythe:auth-changed'));
    }

    // ── Core Auth Logic ──────────────────────────────────────────────────────
    // JWT helpers: decode payload and check expiry (no crypto verification)
    function decodeJwt(token) {
        try {
            if (!token || typeof token !== 'string') return null;
            const parts = token.split('.');
            if (parts.length !== 3) return null;
            const payload = parts[1];
            const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
            const pad = base64.length % 4;
            const padded = pad ? base64 + '='.repeat(4 - pad) : base64;
            const json = atob(padded);
            return JSON.parse(json);
        } catch (e) {
            console.error('[Auth] JWT decode failed', e);
            return null;
        }
    }

    function isJwtExpired(payload) {
        if (!payload || !payload.exp) return true;
        try {
            return Date.now() >= payload.exp * 1000;
        } catch (e) {
            return true;
        }
    }

    // Instrumentation helper to debug auth state during migration
    if (!window.__SCYTHE_DEBUG_AUTH__) {
        window.__SCYTHE_DEBUG_AUTH__ = {
            TOKEN_KEY,
            COOKIE_NAME,
            IS_BROKER,
            runtime: window.SCYTHE_RUNTIME,
            dump() {
                console.group('[SCYTHE AUTH DEBUG]');
                try {
                    console.log('Role:', IS_BROKER ? 'broker' : 'instance');
                    console.log('TOKEN_KEY:', TOKEN_KEY);
                    console.log('sessionStorage:', sessionStorage.getItem(TOKEN_KEY));
                    console.log('localStorage:', localStorage.getItem(TOKEN_KEY));
                    console.log('cookie:', getCookie(COOKIE_NAME));
                    console.log('transport token:', ScytheTransport.getSessionToken?.());
                } catch (e) {
                    console.warn('[SCYTHE AUTH DEBUG] dump failed', e);
                }
                console.groupEnd();
            }
        };
    }

    async function validateSession(token) {
        // Local JWT-first validation (no network call for JWTs)
        if (!token) return { valid: false };

        try {
            if (typeof token === 'string' && token.split('.').length === 3) {
                const payload = decodeJwt(token);
                if (!payload) return { valid: false };
                if (isJwtExpired(payload)) return { valid: false };

                // Optional issuer/audience checks (configurable via globals)
                const issuer = window.SCYTHE_AUTH_ISSUER || (window.__SCYTHE_BOOTSTRAP__ && window.__SCYTHE_BOOTSTRAP__.auth_issuer) || null;
                const audience = window.SCYTHE_AUTH_AUDIENCE || (window.__SCYTHE_BOOTSTRAP__ && window.__SCYTHE_BOOTSTRAP__.auth_audience) || null;

                if (issuer && payload.iss && payload.iss !== issuer) {
                    console.warn('[Auth] JWT issuer mismatch', payload.iss, '!=', issuer);
                    return { valid: false };
                }

                if (audience) {
                    const aud = payload.aud;
                    const audMatch = Array.isArray(aud) ? aud.indexOf(audience) !== -1 : (aud === audience);
                    if (!audMatch) {
                        console.warn('[Auth] JWT audience mismatch', aud, '!=', audience);
                        return { valid: false };
                    }
                }

                if (IS_BROKER) {
                    const resp = await ScytheTransport.fetch('/api/operator/session', {
                        headers: { 'X-Session-Token': token }
                    });
                    if (!resp.ok) return { valid: false };
                }

                const operator = {
                    operator_id: payload.sub || null,
                    callsign: payload.preferred_username || payload.username || (payload.email ? payload.email.split('@')[0] : null),
                    email: payload.email || null,
                    role: payload.role || (payload.roles && payload.roles[0]) || 'operator'
                };

                // Cache decoded claims for reuse and expose globally for other modules
                currentClaims = payload;
                try { window.__SCYTHE_CLAIMS__ = payload; } catch (e) { /* noop */ }

                // Persist token locally and set transport token
                persistSession(token);

                return { valid: true, operator, claims: payload };
            }

            // Legacy: non-JWT tokens still validated via server endpoint
            const resp = await ScytheTransport.fetch('/api/operator/session', { 
                headers: { 'X-Session-Token': token } 
            });
            if (!resp.ok) return { valid: false };
            const data = await resp.json();
            const valid = data.status === 'ok';
            if (valid) {
                // Try to cache any claims returned by legacy session endpoint
                currentClaims = data.claims || null;
                try { window.__SCYTHE_CLAIMS__ = data.claims || null; } catch (e) { /* noop */ }

                persistSession(token);
            }
            return { valid, operator: data.operator, claims: data.claims || null };

        } catch (e) {
            console.error('[Auth] Validation error:', e);
            return { valid: false };
        }
    }

    async function login(callsign, password, instanceId = null) {
        // 1. Authenticate with Orchestrator (Orchestrator plane logic)
        const resp = await ScytheTransport.fetch('/api/operator/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({callsign, password})
        });
        const data = await resp.json();
        
        if (data.status !== 'ok') {
            return { success: false, message: data.message || 'Login failed' };
        }

        const token = data.session?.session_token || data.session?.sessionToken || data.session?.token;

        // 2. If no instanceId is provided, we remain in Orchestrator realm (minimal persistence)
        if (!instanceId) {
            persistSession(token);
            try { window.__SCYTHE_DEBUG_AUTH__?.dump?.(); } catch (e) { /* noop */ }
            window.dispatchEvent(new Event('scythe:login-success'));
            return { success: true, data };
        }

        // 3. Instance bootstrap flow: Mint bootstrap token
        console.log('[Auth] Minting bootstrap token for:', instanceId);
        const bootstrapResp = await ScytheTransport.fetch('/api/operator/issue-bootstrap', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-Session-Token': token 
            },
            body: JSON.stringify({ instance_id: instanceId })
        });
        
        const bootstrapData = await bootstrapResp.json();
        if (bootstrapData.status === 'ok') {
            // Redirect to Instance Realm (transiently, no persistent orchestrator session)
            const redirectUrl = `/scythe/i/${instanceId}/command-ops-visualization.html?bootstrap_token=${encodeURIComponent(bootstrapData.bootstrap_token)}`;
            window.location.href = redirectUrl;
            return { success: true, redirecting: true };
        }
        
        return { success: false, message: 'Failed to mint bootstrap token' };
    }

    async function register(callsign, email, password) {
        const resp = await ScytheTransport.fetch('/api/operator/register', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({callsign, email, password})
        });
        const data = await resp.json();
        return { success: data.status === 'ok', message: data.message };
    }

    async function setupTOTP() {
        const token = restoreSession();
        if (!token) return { success: false, message: 'Not authenticated' };
        
        const resp = await ScytheTransport.fetch('/api/operator/totp/setup', {
            method: 'POST',
            headers: { 'X-Session-Token': token }
        });
        const data = await resp.json();
        return { 
            success: data.status === 'ok', 
            secret: data.secret,
            qrCodeUrl: data.qr_code_url,
            message: data.message 
        };
    }

    async function enableTOTP(secret, code) {
        const token = restoreSession();
        if (!token) return { success: false, message: 'Not authenticated' };
        
        const resp = await ScytheTransport.fetch('/api/operator/totp/enable', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-Session-Token': token 
            },
            body: JSON.stringify({ secret, code })
        });
        const data = await resp.json();
        return { success: data.status === 'ok', message: data.message };
    }

    async function verifyTOTP(callsign, totpCode) {
        const resp = await ScytheTransport.fetch('/api/operator/totp/verify', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ callsign, totp_code: totpCode })
        });
        const data = await resp.json();
        if (data.status === 'ok') {
            const token = data.session?.session_token || data.session?.sessionToken || data.session?.token;
            if (token) {
                persistSession(token);
                try { window.__SCYTHE_DEBUG_AUTH__?.dump?.(); } catch (e) { /* noop */ }
                window.dispatchEvent(new Event('scythe:login-success'));
            }
            window.dispatchEvent(new Event('scythe:auth-changed'));
            return { success: true, data };
        }
        return { success: false, message: data.message || 'TOTP verification failed' };
    }

    async function getTOTPStatus() {
        const token = restoreSession();
        if (!token) return { success: false, message: 'Not authenticated' };
        
        const resp = await ScytheTransport.fetch('/api/operator/totp/status', {
            headers: { 'X-Session-Token': token }
        });
        const data = await resp.json();
        return { 
            success: data.status === 'ok', 
            totpEnabled: data.totp_enabled,
            message: data.message 
        };
    }

    function getClaims() {
        return currentClaims;
    }

    function isAuthenticated() {
        const token = restoreSession();
        if (!token) return false;
        if (typeof token === 'string' && token.split('.').length === 3) {
            const payload = decodeJwt(token);
            if (!payload) return false;
            return !isJwtExpired(payload);
        }
        // legacy opaque token
        return !!token;
    }

    async function requireAuth(options = {}) {
        const force = options.force === true;
        const token = restoreSession();

        const nextPath = window.location.pathname + window.location.search;
        const loginUrl = new URL('/login.html', window.location.origin);
        loginUrl.searchParams.set('next', nextPath);
        const instanceId = getInstanceIdFromPath();
        if (instanceId) {
            loginUrl.searchParams.set('instance', instanceId);
        }
        if (force) {
            loginUrl.searchParams.set('force', '1');
        }

        if (!token || force) {
            window.location.href = loginUrl.toString();
            return false;
        }

        const result = await validateSession(token);

        if (!result.valid) {
            logout();
            window.location.href = loginUrl.toString();
            return false;
        }

        return true;
    }

    return {
        getApiBase,
        persistSession,
        restoreSession,
        validateSession,
        login,
        register,
        logout,
        setupTOTP,
        enableTOTP,
        verifyTOTP,
        getTOTPStatus,
        getClaims,
        isAuthenticated,
        requireAuth
    };
})();

window.SCYTHE_AUTH = SCYTHE_AUTH;
