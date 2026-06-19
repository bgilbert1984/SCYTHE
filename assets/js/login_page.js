/**
 * SCYTHE Login Page Controller (login_page.js)
 *
 * Handles login form submission, TOTP verification, and redirect flow.
 */

(function initLoginPage() {
    'use strict';

    console.log('[Login Page] Initializing...');

    const form = document.getElementById('loginForm');
    const callsignInput = document.getElementById('callsign');
    const passwordInput = document.getElementById('password');
    const submitBtn = document.getElementById('submitBtn');
    const statusMessage = document.getElementById('statusMessage');
    const totpSection = document.getElementById('totpSection');
    const totpCodeInput = document.getElementById('totpCode');
    const verifyTotpBtn = document.getElementById('verifyTotpBtn');
    const skipTotpBtn = document.getElementById('skipTotpBtn');

    // State for tracking credentials during TOTP flow
    let currentCredentials = {
        callsign: null,
        password: null,
        instanceId: null
    };

    /**
     * Display status message to user
     */
    function setStatus(message, type = 'info') {
        statusMessage.textContent = message;
        statusMessage.className = `status-message ${type}`;

        if (type === 'error') {
            console.error('[Login]', message);
        } else if (type === 'success') {
            console.log('[Login]', message);
        }
    }

    /**
     * Clear status message
     */
    function clearStatus() {
        statusMessage.textContent = '';
        statusMessage.className = 'status-message';
    }

    /**
     * Disable form during submission
     */
    function disableForm() {
        callsignInput.disabled = true;
        passwordInput.disabled = true;
        submitBtn.disabled = true;
    }

    /**
     * Enable form for user input
     */
    function enableForm() {
        callsignInput.disabled = false;
        passwordInput.disabled = false;
        submitBtn.disabled = false;
    }

    /**
     * Show TOTP verification UI
     */
    function showTOTPPrompt() {
        totpSection.classList.add('active');
        totpCodeInput.focus();
        callsignInput.disabled = true;
        passwordInput.disabled = true;
        submitBtn.disabled = true;
    }

    /**
     * Hide TOTP verification UI and reset form
     */
    function hideTOTPPrompt() {
        totpSection.classList.remove('active');
        totpCodeInput.value = '';
        enableForm();
    }

    /**
     * Parse URL parameters to check if login should redirect to instance
     */
    function getRequestedInstance() {
        const params = new URLSearchParams(window.location.search);
        let instanceId = params.get('instance');
        if (!instanceId) {
            const next = params.get('next');
            if (next) {
                try {
                    const nextUrl = new URL(next, window.location.origin);
                    const parts = nextUrl.pathname.split('/');
                    const iIdx = parts.indexOf('i');
                    if (iIdx !== -1 && parts.length > iIdx + 1) {
                        instanceId = parts[iIdx + 1];
                    }
                } catch (e) {
                    console.warn('[Login] Failed to parse instance from next param', e);
                }
            }
        }
        return instanceId;
    }

    /**
     * Handle login form submission
     */
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const callsign = callsignInput.value.trim();
        const password = passwordInput.value.trim();
        const instanceId = getRequestedInstance();

        if (!callsign || !password) {
            setStatus('Callsign and password required', 'error');
            return;
        }

        disableForm();
        setStatus('Authenticating...', 'loading');

        try {
            const result = await SCYTHE_AUTH.login(callsign, password, instanceId);

            if (result.redirecting) {
                // Instance bootstrap flow: redirecting to instance
                setStatus('Establishing instance sovereignty...', 'loading');
                // Page will redirect via window.location.href
                return;
            }

            if (result.requiresTOTP) {
                // TOTP required: prompt user for code
                setStatus('Two-factor authentication required', 'info');
                currentCredentials = { callsign, password, instanceId };
                showTOTPPrompt();
                return;
            }

            if (result.success) {
                setStatus('Authentication successful', 'success');
                console.log('[Login] Redirecting...');

                // Use 'next' parameter if available, otherwise default to home
                const params = new URLSearchParams(window.location.search);
                const next = params.get('next') || '/rf_scythe_home.html';

                // Redirect to appropriate page
                setTimeout(() => {
                    window.location.href = next;
                }, 500);
                return;
            }

            setStatus(result.message || 'Authentication failed', 'error');
            enableForm();

        } catch (err) {
            console.error('[Login] Submission error:', err);
            setStatus('Network error during authentication', 'error');
            enableForm();
        }
    });

    /**
     * Handle TOTP verification
     */
    verifyTotpBtn.addEventListener('click', async () => {
        const totpCode = totpCodeInput.value.trim();

        if (!totpCode || totpCode.length !== 6) {
            setStatus('TOTP code must be 6 digits', 'error');
            totpCodeInput.focus();
            return;
        }

        disableForm();
        setStatus('Verifying TOTP...', 'loading');

        try {
            // Use the stored endpoint for TOTP verification
            const result = await SCYTHE_AUTH.verifyTOTP(
                currentCredentials.callsign,
                currentCredentials.password,
                totpCode
            );

            if (result.success) {
                setStatus('TOTP verified', 'success');
                console.log('[Login] TOTP verified, redirecting...');

                // Use 'next' parameter if available, otherwise default to home
                const params = new URLSearchParams(window.location.search);
                const next = params.get('next') || '/rf_scythe_home.html';

                setTimeout(() => {
                    window.location.href = next;
                }, 500);
                return;
            }

            setStatus(result.message || 'TOTP verification failed', 'error');
            enableForm();

        } catch (err) {
            console.error('[Login] TOTP verification error:', err);
            setStatus('Network error during TOTP verification', 'error');
            enableForm();
        }
    });

    /**
     * Handle TOTP skip (proceed without 2FA verification)
     */
    skipTotpBtn.addEventListener('click', () => {
        // Clear TOTP state and return to login form
        hideTOTPPrompt();
        clearStatus();
        callsignInput.value = '';
        passwordInput.value = '';
        currentCredentials = {
            callsign: null,
            password: null,
            instanceId: null
        };
    });

    /**
     * Auto-submit TOTP when user finishes typing (6 digits)
     */
    totpCodeInput.addEventListener('input', (e) => {
        if (e.target.value.length === 6) {
            // Wait slightly for user to see all digits
            setTimeout(() => {
                verifyTotpBtn.click();
            }, 200);
        }
    });

    /**
     * Pre-fill callsign if provided in session
     */
    function restoreFormState() {
        const saved = sessionStorage.getItem('scythe:login:savedCallsign');
        if (saved) {
            callsignInput.value = saved;
            callsignInput.focus();
            passwordInput.focus();
        } else {
            callsignInput.focus();
        }
    }

    /**
     * Save callsign for next login attempt
     */
    function saveFormState() {
        if (callsignInput.value) {
            sessionStorage.setItem('scythe:login:savedCallsign', callsignInput.value);
        }
    }

    // Initialize page
    console.log('[Login Page] Ready');
    console.log('[Login Page] Bootstrap role:', window.__SCYTHE_BOOTSTRAP__.runtime_role);

    // Check if already authenticated
    const existingToken = SCYTHE_AUTH.restoreSession();
    const params = new URLSearchParams(window.location.search);
    const force = params.get('force') === '1' || params.get('force') === 'true';

    if (existingToken && !force) {
        setStatus('Verifying existing session...', 'loading');
        SCYTHE_AUTH.validateSession(existingToken).then(({ valid } = {}) => {
            if (valid) {
                console.log('[Login Page] Existing session valid, redirecting...');
                const next = params.get('next') || '/rf_scythe_home.html';
                window.location.href = next;
                return;
            }

            // Invalid token — clear and show login form
            console.log('[Login Page] Existing session invalid, clearing and showing login form');
            SCYTHE_AUTH.logout();
            clearStatus();
            restoreFormState();
        }).catch((err) => {
            console.warn('[Login Page] Session validation error', err);
            clearStatus();
            restoreFormState();
        });
    } else {
        // No existing token, or force=true supplied in URL
        if (force && existingToken) {
            console.log('[Login Page] Force login requested; ignoring existing session token');
            SCYTHE_AUTH.logout();
        }
        restoreFormState();
    }

    // Save state on form update
    callsignInput.addEventListener('change', saveFormState);

})();
