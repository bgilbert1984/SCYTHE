#!/usr/bin/env python3
"""Tail-style CLI viewer for SCYTHE gRPC control-path forecast patches."""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import sys
from typing import Iterable, List

import grpc

import scythe_pb2
import scythe_pb2_grpc


def _fmt_timestamp_ms(timestamp_ms: int) -> str:
    if timestamp_ms <= 0:
        return "--:--:--.---"
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
    return dt.strftime("%H:%M:%S.%f")[:-3]


def _fmt_percent(value: float) -> str:
    return f"{round(float(value or 0.0) * 100):>3d}%"


def _fmt_distance(value: float) -> str:
    if value <= 0:
        return "n/a"
    return f"{round(value)}m"


def _label_or_id(label: str, entity_id: str) -> str:
    return (label or "").strip() or (entity_id or "").strip() or "unknown"


def _friendly_rpc_error(exc: grpc.RpcError, target: str) -> str:
    details = exc.details() if hasattr(exc, 'details') else str(exc)
    raw = f"{exc}".lower()
    detail_text = f"{details}".lower()
    if 'failed parsing http/2' in raw or 'failed parsing http/2' in detail_text:
        return (
            f"gRPC stream error: {details}\n"
            f"hint: {target} is likely an HTTP or gRPC-Web endpoint, not the native SCYTHE gRPC socket.\n"
            f"      Use the native gRPC backend on 127.0.0.1:50051 for this CLI.\n"
            f"      Instance API ports (like rf_scythe_api_server) and Envoy gRPC-Web ports will not work here."
        )
    if 'invalid or expired session token' in detail_text:
        return (
            f"gRPC stream error: {details}\n"
            f"hint: use a real operator session token, not a placeholder.\n"
            f"      Either pass --token / SCYTHE_SESSION_TOKEN from /api/operator/login,\n"
            f"      or let this CLI log in via --callsign/--password on the same instance."
        )
    if 'deadline exceeded' in detail_text:
        return (
            f"gRPC stream error: {details}\n"
            f"hint: the stream connected, but no control-path patches arrived before the timeout.\n"
            f"      Try a longer --timeout-s, a lower --min-confidence, or verify the observer currently has forecast activity."
        )
    return f"gRPC stream error: {details}"


def _cli_option_present(argv: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in argv)


def _login_via_grpc(
    *,
    target: str,
    instance_id: str,
    callsign: str,
    password: str,
    timeout_s: float,
) -> str:
    channel = grpc.insecure_channel(target)
    try:
        stub = scythe_pb2_grpc.AuthServiceStub(channel)
        response = stub.Login(
            scythe_pb2.LoginRequest(
                instance_id=instance_id,
                callsign=callsign,
                password=password,
            ),
            timeout=timeout_s if timeout_s > 0 else 5.0,
        )
    finally:
        channel.close()
    if not response.success or not response.token:
        raise RuntimeError(response.message or 'Login failed')
    return response.token


def format_control_path_patch(
    patch: scythe_pb2.ControlPathPatch,
    *,
    verbose: bool = False,
) -> List[str]:
    ts = _fmt_timestamp_ms(int(patch.updated_at_ms or 0))
    op = (patch.op or "upsert").upper()
    if op == "DELETE":
        return [
            f"{ts} DELETE {patch.prediction_id or '-'} instance={patch.instance_id or '-'} "
            f"observer={patch.observer_id or '-'}"
        ]

    seed = _label_or_id(patch.current_label, patch.current_entity_id)
    target = _label_or_id(patch.target_label, patch.target_entity_id)
    target_distance = _fmt_distance(float(getattr(patch.projected_target, "distance_m", 0.0) or 0.0))
    lines = [
        (
            f"{ts} {op:<6} {patch.prediction_id or '-'} "
            f"{seed} -> {target} "
            f"conf={_fmt_percent(patch.confidence)} "
            f"horizon={int(patch.time_horizon_s or 0)}s "
            f"motion={len(patch.motion_forecast)} "
            f"path={len(patch.projected_path)} "
            f"target={target_distance} "
            f"phase={patch.temporal_phase or 'n/a'} "
            f"behavior={patch.behavior_class or 'n/a'} "
            f"risk={_fmt_percent(patch.divergence_risk)} "
            f"intent={(patch.top_intent_label or 'n/a')} "
            f"res={_fmt_percent(patch.resilience_score)} "
            f"rf={patch.rf_class or 'n/a'} "
            f"src={patch.candidate_source or 'n/a'}"
        )
    ]

    if not verbose:
        return lines

    if patch.HasField("temporal"):
        lines.append(
            "        "
            f"temporal  pattern={patch.temporal.pattern or 'UNKNOWN'} "
            f"period={patch.temporal.periodicity_s:.2f}s "
            f"pconf={_fmt_percent(patch.temporal.periodicity_confidence)} "
            f"burst={patch.temporal.burstiness:.2f} "
            f"cohesion={patch.temporal.temporal_cohesion:.2f}"
        )

    for point in patch.motion_forecast:
        lines.append(
            "        "
            f"motion[{int(point.step or 0)}] "
            f"t+{int(point.time_offset_s or 0)}s "
            f"lat={point.lat:.6f} lon={point.lon:.6f} alt={point.alt_m:.1f}m "
            f"conf={_fmt_percent(point.confidence)} "
            f"r={round(point.radius_m)}m "
            f"model={point.model or 'n/a'}"
        )
    for point in patch.projected_path:
        lines.append(
            "        "
            f"path[{int(point.step or 0)}] "
            f"dist={_fmt_distance(point.distance_m)} "
            f"bearing={point.absolute_bearing_deg:.1f} "
            f"rel={point.relative_bearing_deg:.1f} "
            f"elev={point.elevation_deg:.1f} "
            f"lat={point.lat:.6f} lon={point.lon:.6f}"
        )
    if patch.HasField("projected_target"):
        point = patch.projected_target
        lines.append(
            "        "
            "target     "
            f"dist={_fmt_distance(point.distance_m)} "
            f"bearing={point.absolute_bearing_deg:.1f} "
            f"rel={point.relative_bearing_deg:.1f} "
            f"elev={point.elevation_deg:.1f} "
            f"lat={point.lat:.6f} lon={point.lon:.6f}"
        )
    return lines


def _iter_stream(
    *,
    target: str,
    token: str | None,
    instance_id: str,
    observer_id: str,
    limit: int,
    max_distance_m: int,
    min_confidence: float,
    timeout_s: float,
) -> Iterable[scythe_pb2.ControlPathPatch]:
    request = scythe_pb2.ControlPathStreamRequest(
        instance_id=instance_id,
        observer_id=observer_id,
        limit=max(1, int(limit)),
        max_distance_m=max(100, int(max_distance_m)),
        min_confidence_milli=max(0, min(1000, int(round(min_confidence * 1000.0)))),
    )
    metadata = [('authorization', f'Bearer {token}')] if token else None
    channel = grpc.insecure_channel(target)
    try:
        stub = scythe_pb2_grpc.ControlPathStreamStub(channel)
        kwargs = {'timeout': timeout_s if timeout_s > 0 else None}
        if metadata:
            kwargs['metadata'] = metadata
        for patch in stub.StreamControlPaths(request, **kwargs):
            yield patch
    finally:
        channel.close()


def main(argv: list[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Tail the SCYTHE gRPC control-path stream")
    parser.add_argument('--target', default='127.0.0.1:50051', help='gRPC target host:port')
    parser.add_argument('--instance-id', required=True, help='SCYTHE instance ID')
    parser.add_argument('--observer-id', required=True, help='Observer / sensor / recon entity ID')
    parser.add_argument('--token', default='', help='Explicit bearer session token')
    parser.add_argument('--callsign', default=os.environ.get('SCYTHE_OPERATOR_CALLSIGN', ''), help='Optional operator callsign for gRPC login')
    parser.add_argument('--password', default=os.environ.get('SCYTHE_OPERATOR_PASSWORD', ''), help='Optional operator password for gRPC login')
    parser.add_argument('--limit', type=int, default=8, help='Forecast limit')
    parser.add_argument('--max-distance-m', type=int, default=10000, help='Projection range limit in meters')
    parser.add_argument('--min-confidence', type=float, default=0.6, help='Minimum confidence 0.0-1.0')
    parser.add_argument('--timeout-s', type=float, default=0.0, help='Optional RPC timeout in seconds (0 = no timeout)')
    parser.add_argument('--login-timeout-s', type=float, default=5.0, help='Optional gRPC login timeout in seconds')
    parser.add_argument('--once', action='store_true', help='Exit after the first patch')
    parser.add_argument('--verbose', action='store_true', help='Print motion/path details under each patch')
    args = parser.parse_args(argv_list)

    if (args.callsign and not args.password) or (args.password and not args.callsign):
        parser.error('Provide both --callsign and --password for gRPC login')

    token = args.token or None
    explicit_token_supplied = _cli_option_present(argv_list, '--token')
    if token is None and args.callsign and args.password and not explicit_token_supplied:
        try:
            token = _login_via_grpc(
                target=args.target,
                instance_id=args.instance_id,
                callsign=args.callsign,
                password=args.password,
                timeout_s=args.login_timeout_s,
            )
            print(f"Authenticated via gRPC Login as {args.callsign}")
        except RuntimeError as exc:
            print(f"gRPC login failed: {exc}", file=sys.stderr)
            return 1
        except grpc.RpcError as exc:
            print(_friendly_rpc_error(exc, args.target), file=sys.stderr)
            return 1
    if token is None:
        token = os.environ.get('SCYTHE_SESSION_TOKEN', '') or None

    print(
        f"SCYTHE ControlPathStream target={args.target} "
        f"instance={args.instance_id} observer={args.observer_id} "
        f"min_conf={args.min_confidence:.2f}"
    )
    try:
        for patch in _iter_stream(
            target=args.target,
            token=token,
            instance_id=args.instance_id,
            observer_id=args.observer_id,
            limit=args.limit,
            max_distance_m=args.max_distance_m,
            min_confidence=args.min_confidence,
            timeout_s=args.timeout_s,
        ):
            for line in format_control_path_patch(patch, verbose=args.verbose):
                print(line, flush=True)
            if args.once:
                break
    except grpc.RpcError as exc:
        print(_friendly_rpc_error(exc, args.target), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
