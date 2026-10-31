# Creating the first administrator

A server never creates an administrator by itself, and no web request can: the first one is made on the host, with a command, and only while none exists.

```
python3 tools/scythe_admin.py bootstrap-admin --callsign NAME --email ADDRESS
```

It asks for the password twice (at least 12 characters). For an unattended install, give a file instead:

```
install -m 600 /dev/null /root/admin.pw      # then put the password in it
python3 tools/scythe_admin.py bootstrap-admin --callsign NAME --email ADDRESS --password-file /root/admin.pw
```

The file must be a regular file that only its owner can read or write, or the command refuses to use it. Delete it afterwards. The password is never taken from the command line.

- It creates a new account; it never changes an existing one.
- It refuses (exit status 2) when an administrator already exists, so it cannot be run twice. Two runs at once cannot both succeed.
- The creation is recorded in the `admin_audit_log` table of the operator database (event, account, time; no password).
- `--db PATH` picks the operator database; by default it is the one the servers use (`OP_SESSION_DB_PATH` overrides it).

Exit status: 0 created, 2 an administrator already exists, 1 anything else.

## Databases made by earlier versions

Earlier versions created an `ADMIN` account on an empty database. Starting a server no longer does that, but it does not remove one that exists. If your database has an `ADMIN` account you did not create yourself, or you never changed its password, treat it as untrusted: create your own administrator, then change or remove that account.
