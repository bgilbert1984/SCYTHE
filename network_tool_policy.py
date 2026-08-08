"""Strict execution policy for external network-analysis tools.

This module contains no scanner implementation.  It only validates the small,
declared subset of Nmap and nDPI inputs that SCYTHE is willing to execute.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shlex
from typing import Iterable


DEFAULT_NMAP_NETWORKS = (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10",
)
_NO_ARGUMENT = frozenset(("-sn", "-sT", "-sV", "-Pn", "-n", "--traceroute"))
_PORT_EXPRESSION = re.compile(r"^(?:T:|U:)?[0-9,-]{1,128}$")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")


class NetworkToolPolicyError(ValueError):
    pass


def configured_nmap_networks(value: str | None = None) -> tuple[ipaddress._BaseNetwork, ...]:
    raw = value if value is not None else os.environ.get("SCYTHE_NMAP_ALLOWED_NETWORKS", "")
    entries = [item.strip() for item in raw.split(",") if item.strip()] or list(DEFAULT_NMAP_NETWORKS)
    try:
        return tuple(ipaddress.ip_network(item, strict=False) for item in entries)
    except ValueError as exc:
        raise NetworkToolPolicyError(f"invalid SCYTHE_NMAP_ALLOWED_NETWORKS: {exc}") from exc


def validate_nmap_target(target: object, allowed: Iterable[ipaddress._BaseNetwork] | None = None) -> str:
    value = str(target or "").strip()
    if not value or len(value) > 64 or value.startswith("-"):
        raise NetworkToolPolicyError("Nmap target must be one IP address or CIDR")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise NetworkToolPolicyError("hostnames are not accepted; use an allowed IP address or CIDR") from exc
    permitted = tuple(allowed) if allowed is not None else configured_nmap_networks()
    if not any(network.version == boundary.version and network.subnet_of(boundary) for boundary in permitted):
        raise NetworkToolPolicyError("Nmap target is outside SCYTHE_NMAP_ALLOWED_NETWORKS")
    return value


def validate_nmap_options(options: object) -> list[str]:
    try:
        tokens = shlex.split(str(options or "-sn"))
    except ValueError as exc:
        raise NetworkToolPolicyError(f"invalid Nmap options: {exc}") from exc
    if not tokens or len(tokens) > 16:
        raise NetworkToolPolicyError("Nmap options must contain 1-16 allow-listed tokens")
    validated: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _NO_ARGUMENT or re.fullmatch(r"-T[0-4]", token):
            validated.append(token); index += 1; continue
        if token == "-p":
            if index + 1 >= len(tokens) or not _PORT_EXPRESSION.fullmatch(tokens[index + 1]):
                raise NetworkToolPolicyError("-p requires a bounded numeric port expression")
            validated.extend((token, tokens[index + 1])); index += 2; continue
        if token == "--top-ports":
            if index + 1 >= len(tokens) or not tokens[index + 1].isdigit() or not 1 <= int(tokens[index + 1]) <= 1000:
                raise NetworkToolPolicyError("--top-ports must be between 1 and 1000")
            validated.extend((token, tokens[index + 1])); index += 2; continue
        raise NetworkToolPolicyError(f"Nmap option is not allow-listed: {token}")
    return validated


def validate_ndpi_request(interface: object, duration: object) -> tuple[str, int]:
    name = str(interface or "eth0").strip()
    if not _INTERFACE.fullmatch(name):
        raise NetworkToolPolicyError("nDPI interface name is invalid")
    allowed = tuple(item.strip() for item in os.environ.get("SCYTHE_NDPI_ALLOWED_INTERFACES", "eth0").split(",") if item.strip())
    if name not in allowed:
        raise NetworkToolPolicyError("nDPI interface is outside SCYTHE_NDPI_ALLOWED_INTERFACES")
    try:
        seconds = int(duration)
    except (TypeError, ValueError) as exc:
        raise NetworkToolPolicyError("nDPI duration must be an integer") from exc
    if not 1 <= seconds <= 60:
        raise NetworkToolPolicyError("nDPI duration must be between 1 and 60 seconds")
    return name, seconds


def parse_nmap_report_identity(line: str) -> tuple[str, str]:
    """Return ``(ip, hostname)`` from a standard Nmap report header."""
    marker = "Nmap scan report for"
    if marker not in line:
        raise NetworkToolPolicyError("not an Nmap report header")
    report = line.split(marker, 1)[1].strip()
    if report.endswith(")") and "(" in report:
        hostname, parenthesized_ip = report.rsplit("(", 1)
        return parenthesized_ip[:-1].strip(), hostname.strip()
    return report, report
