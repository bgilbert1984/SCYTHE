# Password storage

Operator and private-room passwords are stored as
`pbkdf2_sha256$<iterations>$<base64 salt>$<base64 digest>`: PBKDF2-HMAC-SHA256, 600,000
iterations (the OWASP 2023 minimum), a random 16-byte salt per hash.

- **Old rows.** Rows written before this change hold an unsalted-in-effect SHA-256 digest (one fixed
  salt for everybody). They still verify, and are replaced with the new format on the next successful
  login or room join. Until a user logs in once, their row is still the weak one; a database copied
  before then can be attacked offline at SHA-256 speed.
- **Raising the cost later.** Change `PASSWORD_HASH_ITERATIONS`; rows with a lower count are replaced
  on the next successful login. A stored count above `PASSWORD_HASH_MAX_ITERATIONS` is treated as
  corrupt and never computed.
- **Unknown callsign.** Costs the same hashing work as a wrong password, so response time does not
  reveal which callsigns exist. A legacy row is cheaper to check than a new one, so that difference is
  visible until the row is replaced.
- **Empty hashes.** Operators created through the identity provider hold an empty hash; nothing
  matches it.

Not changed here: sessions and bearer tokens, the identity-provider path, and the TOTP branch of the
orchestrator login, which passes the stored hash to `authenticate()` as if it were the password (that
could never match before, and cannot now).
