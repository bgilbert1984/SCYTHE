# Login `next` harness

`login.html?next=<value>` sends the operator on after sign-in, so a page that needs a session can bring them back (deep links
rely on it). `assets/js/safe_next.js` lets the login page follow `next` only when it is a path on its own origin; anything else
goes to `/rf_scythe_home.html`.

    PLAYWRIGHT_BROWSERS_PATH=… python e2e/login_next/run.py

The harness gives the browser a valid session, so the page redirects at once, and checks where it lands for a same-origin path,
a same-origin full URL, another host, a protocol-relative URL, a backslash host and a `javascript:` URL. It also fails if the
other host was requested or script ran. With the pre-fix `login_page.js` the three host cases fail; the `javascript:` case never
redirects in headless Chromium, so it fails by timeout, and this harness does not show whether script would have run there.

Exit status: 0 passed, 1 a case failed, 2 infrastructure failure. Unit tests of the rule: `scythe-web/safeNext.test.js`.
