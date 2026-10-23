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

    if (typeof module !== 'undefined' && module.exports) module.exports = { safeNext };
    root.SCYTHE_SAFE_NEXT = { safeNext };
})(typeof window !== 'undefined' ? window : globalThis);
