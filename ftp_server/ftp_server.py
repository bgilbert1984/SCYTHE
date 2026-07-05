import os
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from pyftpdlib.authorizers import DummyAuthorizer


def main():
    port = int(os.getenv("FTP_PORT", "2121"))
    directory = os.getenv("FTP_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    passive_ports = os.getenv("FTP_PASV_PORTS", "60000-60010")
    allow_anonymous = os.getenv("FTP_ALLOW_ANONYMOUS", "true").lower() in ("true", "1", "yes")

    os.makedirs(directory, exist_ok=True)

    auth = DummyAuthorizer()
    
    # Add anonymous user with blank password
    if allow_anonymous:
        auth.add_user('anonymous', '', directory, perm='elradfmwMT')
    else:
        # Fallback: add default user if anonymous is disabled
        user = os.getenv("FTP_USER", "codespace")
        pwd = os.getenv("FTP_PASS", "codespace")
        auth.add_user(user, pwd, directory, perm='elradfmwMT')

    handler = FTPHandler
    handler.authorizer = auth

    try:
        start, end = passive_ports.split("-", 1)
        handler.passive_ports = range(int(start), int(end) + 1)
    except Exception:
        pass

    server = FTPServer(("0.0.0.0", port), handler)
    auth_mode = "anonymous (blank password)" if allow_anonymous else f"user={os.getenv('FTP_USER', 'codespace')}"
    print(f"Starting FTP server on 0.0.0.0:{port}\n  auth={auth_mode}\n  dir={directory}")
    server.serve_forever()


if __name__ == '__main__':
    main()
