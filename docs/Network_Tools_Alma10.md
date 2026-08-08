# SCYTHE Nmap and nDPI on AlmaLinux 10

Status: prepared; privileged installation is required.

SCYTHE probes for the literal executables `nmap` and `ndpiReader` on the child
service `PATH`. The AlmaLinux 10 AppStream repository supplies signed Nmap
7.92 packages. The enabled AlmaLinux repositories do not supply nDPI, so the
SCYTHE installer builds the official nDPI 5.0 stable tag at commit
`375f99ef9fb4999d778b57bbeece171b3fa9fba6`.

The source archive is pinned to SHA-256:

```text
7f77ac7f593e846c3a88d347250e8f232a5a26702d705c2d487a316ec16d2f4e
```

Official references:

- [Nmap Linux packages](https://nmap.org/download.html)
- [nDPI 5.0 release](https://github.com/ntop/nDPI/releases/tag/5.0)
- [nDPI build instructions](https://github.com/ntop/nDPI#how-to-compile-ndpi)

## Installation

Run the audited installer from an interactive shell:

```bash
sudo /home/spectrcyde/SCYTHE/scripts/install_network_tools_alma10.sh
```

The installer:

1. Refuses non-root execution and non-AlmaLinux-10 hosts.
2. Installs Nmap and nDPI build prerequisites from enabled signed Alma repos.
3. Downloads the exact nDPI commit over HTTPS.
4. Verifies the archive SHA-256 before extraction.
5. Builds and installs nDPI under `/usr/local`.
6. Gives only `ndpiReader` `CAP_NET_RAW` and `CAP_NET_ADMIN`, which are required
   for the bounded live-interface capture endpoint.
7. Prints Nmap and nDPI verification output.

Set `SCYTHE_NDPI_RUN_CHECKS=1` on the sudo command to run the upstream nDPI
test suite before installation. This takes substantially longer.

Restart the SCYTHE instance after installation so its startup banner is
regenerated. The status endpoints probe on demand, but the existing log banner
is historical and cannot change in place.

## Execution and evidence boundary

Installing the tools does not make their output authoritative by itself.
SCYTHE now applies these rules before executing either binary:

- active scan and DPI routes require an authenticated operator;
- absent binaries produce `unavailable`, never fabricated scan observations;
- Nmap accepts IP addresses and CIDRs only;
- Nmap defaults to RFC1918, loopback, link-local, CGNAT, and IPv6 ULA ranges;
- Nmap scripts, output-file options, SYN scans, shell tokens, and timing `T5`
  are refused;
- explicit port scans are limited to numeric expressions and at most 1,000
  top ports;
- nDPI defaults to `eth0`, with interface names and capture time validated;
- nDPI capture duration is limited to 1-60 seconds;
- a failed scan cannot erase the previously rendered network hypergraph.

Additional Nmap boundaries can be declared before starting the orchestrator:

```bash
SCYTHE_NMAP_ALLOWED_NETWORKS=192.168.1.0/24,10.20.0.0/16
```

Additional nDPI interfaces require an explicit allow-list:

```bash
SCYTHE_NDPI_ALLOWED_INTERFACES=eth0,lo
```

The current WSL2 `eth0` interface represents the WSL virtual network. It does
not replace the Windows Npcap/Suricata Wi-Fi sensor and should not be described
as visibility into every host or packet on the physical LAN.

## Verification

After installation and instance restart:

```bash
command -v nmap ndpiReader
nmap --version | head -1
ndpiReader -h 2>&1 | head -3
getcap /usr/local/bin/ndpiReader
```

The new instance log should report both executables as available. A real scan
or capture still needs a valid operator session and a target/interface inside
the declared policy boundary.
