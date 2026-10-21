"""Caller identity at the MCP boundary.

Authentication used to end in a bool, so by the time a proposal was created or a
tool executed, who made the call had been thrown away.  A ``Principal`` is what
the auth step established; it is carried in a context variable for the duration
of one request and read when a proposal is created, when it is executed, and when
the execution is audited.

``kind`` states how strong the identification is:

  operator_session  a validated operator session; operator_id and session_id set
  internal_token    the shared internal token: a trusted component, no individual
  loopback          loopback caller with no operator manager configured
  anonymous         allowed without identity (development mode)
  unspecified       a custom validator accepted the request without saying who

Only ``operator_session`` identifies an individual.  The raw session token is
never stored here.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, Optional

OPERATOR_SESSION = "operator_session"
INTERNAL_TOKEN = "internal_token"
LOOPBACK = "loopback"
ANONYMOUS = "anonymous"
UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class Principal:
    kind: str
    operator_id: Optional[str] = None
    session_id: Optional[str] = None
    role: Optional[str] = None
    callsign: Optional[str] = None

    @property
    def identified(self) -> bool:
        """True when this names an individual operator."""
        return self.kind == OPERATOR_SESSION and bool(self.operator_id)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None} | {"identified": self.identified}


_principal: contextvars.ContextVar[Optional[Principal]] = contextvars.ContextVar("mcp_principal", default=None)
_proposal_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_proposal_id", default=None)


# Initiator class (ADR 0005 Decision 5).  Assigned by server code, never read from request data.
AUTONOMOUS = "autonomous"
OPERATOR_COMMAND = "operator_command"
INITIATOR_CLASSES = (AUTONOMOUS, OPERATOR_COMMAND)

_initiator: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_initiator", default=None)
_source: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_source", default="mcp")


def current_principal() -> Optional[Principal]:
    return _principal.get()


def current_proposal_id() -> Optional[str]:
    return _proposal_id.get()


def current_initiator() -> Optional[str]:
    """The initiator class of the proposal now executing, or None outside a proposal."""
    return _initiator.get()


def current_source() -> str:
    """The entry point that asked for the running action: ``mcp`` unless an adapter says otherwise."""
    return _source.get()


def principal_dict() -> Optional[Dict[str, Any]]:
    p = _principal.get()
    return p.to_dict() if p is not None else None


@contextlib.contextmanager
def principal_scope(principal: Optional[Principal]) -> Iterator[None]:
    token = _principal.set(principal)
    try:
        yield
    finally:
        _principal.reset(token)


@contextlib.contextmanager
def proposal_scope(proposal_id: Optional[str]) -> Iterator[None]:
    token = _proposal_id.set(proposal_id)
    try:
        yield
    finally:
        _proposal_id.reset(token)


@contextlib.contextmanager
def initiator_scope(initiator: Optional[str]) -> Iterator[None]:
    token = _initiator.set(initiator)
    try:
        yield
    finally:
        _initiator.reset(token)


@contextlib.contextmanager
def source_scope(source: str) -> Iterator[None]:
    """Name the entry point (for example ``api.sdr.tune``) so execution rows can tell it from ``mcp``."""
    token = _source.set(source)
    try:
        yield
    finally:
        _source.reset(token)


def coerce(result: Any) -> Optional[Principal]:
    """Normalize an auth validator's return value into a Principal or None."""
    if isinstance(result, Principal):
        return result
    return Principal(kind=UNSPECIFIED) if result else None
