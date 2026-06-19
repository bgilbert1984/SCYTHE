/**
 * SCYTHE Registration Page Controller (register_page.js)
 *
 * Handles operator registration form submission and validation.
 */

(function initRegisterPage() {
    'use strict';

    console.log('[Register Page] Initializing...');

    const form = document.getElementById('registerForm');
    const callsignInput = document.getElementById('callsign');
    const emailInput = document.getElementById('email');
    const passwordInput = document.getElementById('password');
    const confirmPasswordInput = document.getElementById('confirmPassword');
    const submitBtn = document.getElementById('submitBtn');
    const statusMessage = document.getElementById('statusMessage');

    /**
     * Display status message to user
     */
    function setStatus(message, type = 'info') {
        statusMessage.textContent = message;
        statusMessage.className = `status-message ${type}`;

        if (type === 'error') {
            console.error('[Register]', message);
        } else if (type === 'success') {
            console.log('[Register]', message);
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
        emailInput.disabled = true;
        passwordInput.disabled = true;
        confirmPasswordInput.disabled = true;
        submitBtn.disabled = true;
    }

    /**
     * Enable form for user input
     */
    function enableForm() {
        callsignInput.disabled = false;
        emailInput.disabled = false;
        passwordInput.disabled = false;
        confirmPasswordInput.disabled = false;
        submitBtn.disabled = false;
    }

    /**
     * Validate email format
     */
    function isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    /**
     * Validate callsign format (alphanumeric, underscores, hyphens)
     */
    function isValidCallsign(callsign) {
        return /^[a-zA-Z0-9_-]+$/.test(callsign) && callsign.length >= 3 && callsign.length <= 32;
    }

    /**
     * Validate password strength
     */
    function isValidPassword(password) {
        // Minimum 12 characters, at least one uppercase, one lowercase, one digit
        return password.length >= 12 &&
               /[A-Z]/.test(password) &&
               /[a-z]/.test(password) &&
               /[0-9]/.test(password);
    }

    /**
     * Handle registration form submission
     */
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const callsign = callsignInput.value.trim();
        const email = emailInput.value.trim();
        const password = passwordInput.value;
        const confirmPassword = confirmPasswordInput.value;

        clearStatus();

        // Validation
        if (!callsign || !email || !password || !confirmPassword) {
            setStatus('All fields are required', 'error');
            return;
        }

        if (!isValidCallsign(callsign)) {
            setStatus('Callsign must be 3-32 characters (alphanumeric, -, _)', 'error');
            return;
        }

        if (!isValidEmail(email)) {
            setStatus('Invalid email format', 'error');
            return;
        }

        if (!isValidPassword(password)) {
            setStatus('Password must be at least 12 chars with uppercase, lowercase, and numbers', 'error');
            return;
        }

        if (password !== confirmPassword) {
            setStatus('Passwords do not match', 'error');
            confirmPasswordInput.focus();
            return;
        }

        disableForm();
        setStatus('Creating account...', 'loading');

        try {
            const result = await SCYTHE_AUTH.register(callsign, email, password);

            if (result.success) {
                setStatus('Account created successfully. Redirecting to login...', 'success');
                console.log('[Register] Account created, redirecting to login');

                setTimeout(() => {
                    window.location.href = '/login.html';
                }, 1500);
                return;
            }

            setStatus(result.message || 'Registration failed', 'error');
            enableForm();

        } catch (err) {
            console.error('[Register] Submission error:', err);
            setStatus('Network error during registration', 'error');
            enableForm();
        }
    });

    /**
     * Real-time password validation feedback
     */
    passwordInput.addEventListener('input', () => {
        if (passwordInput.value && !isValidPassword(passwordInput.value)) {
            passwordInput.style.borderColor = 'rgba(244, 67, 54, 0.5)';
        } else if (passwordInput.value) {
            passwordInput.style.borderColor = 'rgba(0, 206, 201, 0.2)';
        } else {
            passwordInput.style.borderColor = 'rgba(0, 206, 201, 0.2)';
        }
    });

    /**
     * Real-time password match validation
     */
    confirmPasswordInput.addEventListener('input', () => {
        if (confirmPasswordInput.value && passwordInput.value !== confirmPasswordInput.value) {
            confirmPasswordInput.style.borderColor = 'rgba(244, 67, 54, 0.5)';
        } else if (confirmPasswordInput.value && passwordInput.value === confirmPasswordInput.value) {
            confirmPasswordInput.style.borderColor = 'rgba(0, 206, 201, 0.2)';
        } else {
            confirmPasswordInput.style.borderColor = 'rgba(0, 206, 201, 0.2)';
        }
    });

    // Initialize page
    console.log('[Register Page] Ready');
    console.log('[Register Page] Bootstrap role:', window.__SCYTHE_BOOTSTRAP__.runtime_role);

    // Focus on first field
    callsignInput.focus();

})();
