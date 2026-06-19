/**
 * SCYTHE Runtime Transport (scythe_transport.js)
 * 
 * Formalizes transport topology authority for distributed SCYTHE instances.
 * Prevents transport plane bleeding by centralizing API and WS origin resolution.
 */

(function() {
    'use strict';

    try {
        // 1. Immutable Runtime Topology
        // Injected via server bootstrap if available
        const bootstrap = window.__SCYTHE_BOOTSTRAP__ || {};
        const runtimeRole = bootstrap.runtime_role || (bootstrap.instance_id ? 'instance' : 'broker');
        
        const runtime = Object.freeze({
            instanceId: bootstrap.instance_id || 'unknown',
            apiBase: bootstrap.api_base || window.API_BASE || window.location.origin,
            wsBase: bootstrap.ws_base || window.location.origin.replace(/^http/, 'ws'),
            pathPrefix: bootstrap.path_prefix || '',
            socketioPath: bootstrap.socketio_path || '/socket.io',
            topology: runtimeRole === 'instance' ? 'instance' : 'orchestrator',
            runtimeRole: runtimeRole,
            storagePrefix: runtimeRole === 'instance' ? `scythe:${bootstrap.instance_id || 'unknown'}` : 'scythe:orchestrator',
            bootEpoch: Date.now(),
            topologyVersion: '1.0.0'
        });

        window.SCYTHE_RUNTIME = runtime;

        // Fatal topology violation check
        document.addEventListener('DOMContentLoaded', () => {
            if (window.location.pathname.includes('/scythe/i/') && (!bootstrap.instance_id || runtime.instanceId === 'unknown')) {
                window.stop();
                document.body.innerHTML = '<h1>Fatal topology bootstrap failure</h1>';
                throw new Error('[SCYTHE] Fatal topology violation: instance bootstrap metadata missing');
            }
        });

        console.log('[ScytheTransport] Initialized topology:', runtime);

        // 2. Transport Abstraction
        let activeSessionToken = null;

        window.ScytheTransport = {
            setSessionToken(token) {
                activeSessionToken = token;
            },

            clearSessionToken() {
                activeSessionToken = null;
            },

            resolve(path) {
                if (/^https?:\/\//.test(path)) {
                    console.warn("[ScytheTransport] Absolute URL bypass detected:", path);
                    return path;
                }
                // Ensure path starts with /
                const normalizedPath = path.startsWith('/') ? path : `/${path}`;
                return `${window.SCYTHE_RUNTIME.apiBase}${normalizedPath}`;
            },

            async fetch(path, options = {}) {
                const url = this.resolve(path);
                const headers = options.headers || {};

                // Add request correlation IDs
                const reqId = crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).substr(2);
                headers['X-SCYTHE-REQUEST-ID'] = reqId;
                headers['X-SCYTHE-INSTANCE'] = window.SCYTHE_RUNTIME.instanceId;
                headers['X-SCYTHE-TOPOLOGY'] = window.SCYTHE_RUNTIME.topology;

                // Inject session token if available via explicit transport state
                if (activeSessionToken) {
                    headers['X-Session-Token'] = activeSessionToken;
                    // JWT-native authentication header
                    headers['Authorization'] = `Bearer ${activeSessionToken}`;
                }

                options.headers = headers;

                return await fetch(url, options);
            },
            websocket(path, ioOptions = {}) {
                // Explicit binding to our authoritative WS base
                const url = `${window.SCYTHE_RUNTIME.wsBase}${path}`;
                console.log('[ScytheTransport] Connecting WebSocket:', url);
                
                // Defaulting to Socket.IO pattern if io is globally available
                if (typeof io !== 'undefined') {
                    return io(window.SCYTHE_RUNTIME.wsBase, {
                        path: ioOptions.path || window.SCYTHE_RUNTIME.socketioPath,
                        ...ioOptions
                    });
                }
                return new WebSocket(url);
            },

            sse(path) {
                const url = this.resolve(path);
                return new EventSource(url);
            }
        };
        console.log("[ScytheTransport] Exporting global and initialized.");
    } catch(e) {
        console.error("[ScytheTransport] INIT FAILED", e);
    }
})();
