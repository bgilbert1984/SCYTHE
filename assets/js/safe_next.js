/**
 * Where the login page may send the operator afterwards (safe_next.js).
 *
 * The login page takes a `next` query parameter so a page that needs a session can bring the operator back. An unchecked
 * `next` is an open redirect (`?next=https://elsewhere.example`) and, with a `javascript:` value, runs script on this origin.
 * `safeNext` accepts only a path on this origin and returns the parsed path, query and hash, never the raw text; anything else
 * yields the fallback.
 */
(function (root) {
    'use strict';

    function safeNext(raw, origin, fallback) {
        if (typeof raw !== 'string' || raw === '') return fallback;
        let url;
        try { url = new URL(raw, origin); } catch (e) { return fallback; }
        if (url.origin !== origin) return fallback;           // another host, or javascript:/data: (origin "null")
        const path = url.pathname + url.search + url.hash;
        if (!path.startsWith('/') || path.startsWith('//')) return fallback;   // "/.//host" parses to a path that reads as a host
        return path;
    }

    // The instance-id rule (the same as scythe-web/deepLink.js): it is placed in a path, so it must not be able to leave it.
    const INSTANCE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
    function isInstanceId(value) { return typeof value === 'string' && INSTANCE_ID.test(value); }

    if (typeof module !== 'undefined' && module.exports) module.exports = { safeNext, isInstanceId };
    root.SCYTHE_SAFE_NEXT = { safeNext, isInstanceId };
})(typeof window !== 'undefined' ? window : globalThis);
