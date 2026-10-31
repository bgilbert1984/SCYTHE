#!/usr/bin/env python3
"""Administration that must happen on the host, not over HTTP.

    python3 tools/scythe_admin.py bootstrap-admin --callsign NAME --email ADDRESS [--db PATH] [--password-file FILE]

Creates the first administrator. It works only while no administrator exists, creates a new account (it never raises an existing one), and records the creation in the
`admin_audit_log` table. No server creates an administrator on its own or from a request.

The password is never taken from the command line (it would show in the process list and the shell history): it is asked for twice, or read from `--password-file`,
which must be a regular file that only its owner can read.

Exit status: 0 created; 2 an administrator already exists; 1 anything else (bad input, a database problem).
"""
import argparse
import getpass
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the repository root, where operator_session_manager.py lives

from operator_session_manager import AdminAlreadyExists, AdminBootstrapError, OperatorSessionManager, _default_operator_db_path


def read_password_file(path):
    """The password in a file only its owner can read: one line, without the trailing newline."""
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise AdminBootstrapError(f"{path} is not a regular file")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise AdminBootstrapError(f"{path} must not be readable or writable by anyone but its owner (chmod 600)")
    with open(path, encoding="utf8") as handle:
        text = handle.read()
    return text[:-1] if text.endswith("\n") else text


def ask_password():
    first = getpass.getpass("Administrator password: ")
    if first != getpass.getpass("Repeat it: "):
        raise AdminBootstrapError("the two passwords differ")
    return first


def bootstrap_admin(args, manager_class=OperatorSessionManager):
    password = read_password_file(args.password_file) if args.password_file else ask_password()
    manager = manager_class(db_path=args.db or _default_operator_db_path())
    operator = manager.bootstrap_admin(args.callsign, args.email, password)
    print(f"Created administrator {operator.callsign} ({operator.operator_id}).")


class QuietParser(argparse.ArgumentParser):
    """An argument parser whose errors never repeat what was typed: a password given by mistake as an option would otherwise be printed back."""

    def error(self, message):
        if message.startswith("unrecognized arguments"):
            message = "unrecognized arguments (not shown; the password is never accepted on the command line)"
        super().error(message)


def main(argv=None):
    # No abbreviations: `--password secret` would otherwise be read as `--password-file secret` and the secret echoed back in an error.
    parser = QuietParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    boot = commands.add_parser("bootstrap-admin", help="create the first administrator (only while none exists)", allow_abbrev=False)
    boot.add_argument("--callsign", required=True)
    boot.add_argument("--email", required=True)
    boot.add_argument("--db", help="the operator database (default: the one the servers use, or OP_SESSION_DB_PATH)")
    boot.add_argument("--password-file", help="read the password from this file instead of asking (must be mode 600)")
    args = parser.parse_args(argv)
    try:
        bootstrap_admin(args)
    except AdminAlreadyExists as error:
        print(f"Refused: {error}.", file=sys.stderr)
        return 2
    except (AdminBootstrapError, OSError) as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
