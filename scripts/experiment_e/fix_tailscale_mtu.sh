#!/bin/bash
# Lower the tailscale tunnel MTU on a host whose physical NIC cannot carry full-size tunnel packets.
#
# Why: on 2026-11-02 every bulk transfer out of scythe-1 stalled after ~1 KB over the tailnet (SSH output, scp,
# Taildrop), while small packets worked: `tailscale ping --size 1200` passed and --size 1280 got no reply. The tunnel MTU
# was 1280 and eth0 then read 1280 too, so full-size WireGuard packets (tunnel MTU + ~60 bytes) looked too big for
# eth0. Setting TS_DEBUG_MTU=1200 fixed it empirically (tailnet pings pass to 1200, 8 MB over plain SSH in 7.8 s, a
# 245 MB file transferred with the right SHA-256). Caveat: eth0 later read 1360, so something derives it from the
# tunnel MTU and the exact mechanism is not fully explained; trust the measured result, not the story.
#
# Side effect: a tunnel MTU below 1280 cannot carry IPv6 (the kernel drops IPv6 on an interface under 1280), so
# the host's tailnet IPv6 address stops working. Tailnet IPv4 (100.x) is unaffected.
#
# SAFETY: this restarts tailscaled, and Tailscale SSH runs inside it, so your session WILL drop and you must
# reconnect. If the restart goes wrong the host could be unreachable. To prevent a lockout, `apply` first schedules an
# automatic rollback (a separate systemd timer, independent of tailscaled) that restores the old config in 5 minutes.
# You cancel it with `confirm` once you have reconnected and seen the new MTU.
#
#   sudo bash fix_tailscale_mtu.sh status     # read-only; no root needed
#   sudo bash fix_tailscale_mtu.sh apply      # edit config, arm rollback, restart tailscaled in 3 s (session drops)
#   sudo bash fix_tailscale_mtu.sh confirm    # after reconnecting: cancel the automatic rollback
#   sudo bash fix_tailscale_mtu.sh revert     # put the original config back now
set -u
CONF=/etc/default/tailscaled; MTU=${TS_MTU:-1200}; KEY=TS_DEBUG_MTU
BAK_PTR=/var/lib/tailscale/mtu-fix.backup-path

need_root() { [ "$(id -u)" = 0 ] || { echo "must run as root: sudo bash $0 $1" >&2; exit 1; }; }
status() {
  echo "tailscale0 mtu: $(cat /sys/class/net/tailscale0/mtu 2>&1)   eth0 mtu: $(cat /sys/class/net/eth0/mtu 2>&1)"
  echo "config: $(grep -E "^$KEY=" $CONF 2>/dev/null || echo "$KEY not set")"
  systemctl is-active tailscaled 2>&1 | sed 's/^/tailscaled: /'
  systemctl list-timers --all --no-pager 2>/dev/null | grep -E 'ts-mtu' | sed 's/^/rollback armed: /' || true
}
case "${1:-}" in
  status) status ;;
  apply)
    need_root apply
    [ -f "$CONF" ] || { echo "$CONF not found" >&2; exit 1; }
    systemctl list-timers --all --no-pager 2>/dev/null | grep -q ts-mtu-rollback && { echo "a rollback is already armed; run confirm or revert first" >&2; exit 1; }
    BAK="$CONF.bak.$(date +%Y%m%dT%H%M%S)"; cp -a "$CONF" "$BAK"; echo "$BAK" > "$BAK_PTR"
    if grep -q "^$KEY=" "$CONF"; then sed -i "s/^$KEY=.*/$KEY=$MTU/" "$CONF"; else printf '\n# Lower the tunnel MTU: eth0 MTU is 1280, so full-size tunnel packets are dropped.\n%s=%s\n' "$KEY" "$MTU" >> "$CONF"; fi
    echo "config now: $(grep -E "^$KEY=" $CONF)   (backup: $BAK)"
    systemd-run --quiet --unit=ts-mtu-rollback --on-active=300 /bin/sh -c "cp -a '$BAK' '$CONF' && systemctl restart tailscaled" \
      && echo "ROLLBACK ARMED: the old config is restored automatically in 5 minutes unless you run: sudo bash $0 confirm"
    systemd-run --quiet --unit=ts-mtu-apply --on-active=3 systemctl restart tailscaled && echo "tailscaled restarts in 3 s. Your SSH session will drop: reconnect, then run '$0 status' and, if tailscale0 mtu is $MTU, '$0 confirm'."
    ;;
  confirm)
    need_root confirm
    systemctl stop ts-mtu-rollback.timer ts-mtu-rollback.service 2>/dev/null; systemctl reset-failed 'ts-mtu-*' 2>/dev/null
    echo "rollback cancelled."; status ;;
  revert)
    need_root revert
    BAK=$(cat "$BAK_PTR" 2>/dev/null); [ -n "$BAK" ] && [ -f "$BAK" ] || { echo "no backup recorded" >&2; exit 1; }
    systemctl stop ts-mtu-rollback.timer 2>/dev/null; cp -a "$BAK" "$CONF"; echo "restored $BAK; tailscaled restarts in 3 s (session drops)"
    systemd-run --quiet --unit=ts-mtu-revert --on-active=3 systemctl restart tailscaled ;;
  *) sed -n 2,26p "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
