# Simple FTP server for Codespaces

This folder contains a minimal Python FTP server using `pyftpdlib`.

- **Authentication**: Anonymous (blank password) — no credentials needed
- **Default port**: `2121`
- **Default FTP root**: `ftp_server/pcapng`
- **Passive port range**: `60000-60010`

Quick start:

```bash
cd ftp_server
./start_ftp.sh
```

Notes for GitHub Codespaces / forwarded ports:
- Forward port `2121` in the Codespaces Ports view to access the FTP server externally.
- If your FTP client requires passive mode, also forward `60000-60010` and set `FTP_PASV_PORTS=60000-60010` in the environment.

Docker Compose (recommended for Codespaces)

A Docker Compose service is available at `ftp_server/docker-compose.yml`.

From the repository root:

```bash
cd ftp_server
docker compose up -d
```

This starts the FTP service in a container with:
- port `2121` mapped to the host
- passive ports `60000-60010` mapped
- workspace root mounted at `/data`

Environment variables are configured in the compose file and can be overridden by editing `ftp_server/docker-compose.yml`.

Environment variables you can set before running locally:
- `FTP_ALLOW_ANONYMOUS` - enable anonymous access with blank password (default `true`)
- `FTP_USER` - username for non-anonymous mode (default `codespace`, only used if `FTP_ALLOW_ANONYMOUS=false`)
- `FTP_PASS` - password for non-anonymous mode (default `codespace`, only used if `FTP_ALLOW_ANONYMOUS=false`)
- `FTP_PORT` - port to listen on (default `2121`)
- `FTP_DIR` - directory to serve (default workspace root)
- `FTP_PASV_PORTS` - passive port range, e.g. `60000-60010`

Security note: this setup is intended for short-lived development use inside Codespaces. Do not expose sensitive files or use this in production without additional hardening.

Systemd service (optional)

A systemd unit is provided at [ftp_server/scythe-ftp.service](ftp_server/scythe-ftp.service#L1). To install and start it on a system that uses systemd, run the helper script (requires sudo):

```bash
cd ftp_server
chmod +x install_systemd.sh
./install_systemd.sh
```

Notes:
- The install script copies the unit to `/etc/systemd/system/` and enables it. If you're in Codespaces or another environment without systemd, the script will fail — in that case run `./start_ftp.sh` instead.
- Edit environment variables inside the unit file or set them in a drop-in if you need different credentials or paths.
