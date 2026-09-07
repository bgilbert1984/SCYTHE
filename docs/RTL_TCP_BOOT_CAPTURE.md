# rtl_tcp Boot Capture

How the RTL-SDR IQ source comes up after a restart, what is automatic, and what
is not.

The symptom this path exists to prevent:

```text
FFT // NO FRAME RETAINED BY THE BRIDGE · NO TRACE DRAWN
```

That banner is correct behaviour. It means the bridge had no frame and declined
to draw one. The usual cause is simply that `rtl_tcp` is not running.

---

## 1. Design: two uncoupled services

| Service | Owns |
| --- | --- |
| `scythe-rtl-tcp.service` | Availability of the IQ source |
| `scythe-orchestrator.service` (`rf_bridge`) | Reconnection and evidence invalidation |

They are **deliberately not** coupled with `After=` or `Requires=`. `rf_bridge`
already reconnects with exponential backoff capped at `SDRPP_RECONNECT_MAX_S`
(default 10s) and invalidates the IQ ring with `DISCONNECT` on every failure, so
either process may start first and the survivor waits. `Requires=` would convert
a temporarily absent USB device into orchestrator failure for no benefit.

Verified in practice: starting `rtl_tcp` under systemd restored streaming with
no orchestrator restart.

---

## 2. The restart policy is load-bearing

```ini
StartLimitIntervalSec=0
Restart=always
RestartSec=5
```

`rtl_tcp` exits **1** when the device is absent. systemd's default limit is 5
restarts per 10s, after which a unit enters `failed` and stays there.

Measured on this host with a nonexistent device:

| Configuration | Result after 16s |
| --- | --- |
| Default rate limit | `failed`, `NRestarts=5`, `Result=exit-code` |
| `StartLimitIntervalSec=0` | `activating`, `NRestarts=12`, still retrying |

Without the override you would boot, attach the dongle, and find the unit
already dead — reproducing the exact symptom. Unbounded *attempts* are wanted
here; unbounded *frequency* is not, which is what `RestartSec=5` bounds.

`Restart=always` rather than `Restart=on-failure` is intentional: `on-failure`
leaves the unit dead on any clean exit path, and a capture daemon that exits 0
is still a capture daemon that is no longer capturing.

Declared policy, for status surfaces:

```text
RESTART POLICY // UNBOUNDED ATTEMPTS · 5 S INTERVAL
```

### What the bridge does *not* publish

`rf_bridge` does not emit `RTL_TCP // WAITING_FOR_USB`. It cannot: a refused
socket is indistinguishable from a stopped `rtl_tcp`, a wrong endpoint, or a
busy receiver. Naming the cause would be a guess in an operational-status
uniform. It publishes reachability and says so explicitly:

```json
{
  "iq_endpoint": "127.0.0.1:1234",
  "connection_state": "reconnecting",
  "transport_state": "CONNECTING",
  "sample_flow_state": "NONE",
  "availability": "SOURCE_CONNECTING",
  "unreachable_cause": "NOT_DETERMINABLE_FROM_THIS_PROCESS"
}
```

The restart policy above is a property of the unit file, not something the
bridge observes, so it is documented here rather than asserted by a process
that never read it.

---

## 3. Known failure: rtl_tcp survives USB removal

```text
FAILURE // RTL_TCP SURVIVES USB REMOVAL BUT STOPS PRODUCING SAMPLES
SYSTEMD VIEW // PROCESS HEALTHY
DATA-PLANE VIEW // SOURCE STARVED
AUTOMATIC RECOVERY // NOT IMPLEMENTED
MANUAL RECOVERY // RESTART RTL_TCP AFTER USB REATTACH
```

Observed on 2026-09-06. When the NESDR is detached from Windows while
`rtl_tcp` is streaming, `rtl_tcp` does **not** exit. It keeps the process, the
listening socket on 1234 and the established client connection, holding a dead
USB handle and delivering nothing. It also ignores `SIGTERM` in this state;
only `TimeoutStopSec=10` escalating to `SIGKILL` cleared it.

Everything in §2 depends on the daemon exiting. It does not exit here, so:

| Layer | Reports | Reality |
| --- | --- | --- |
| systemd | `active (running)`, `NRestarts=0` | the process is alive and useless |
| `Restart=always` | never fires | there was no exit to restart from |
| `ss -ltnp` | `127.0.0.1:1234` bound | bound by a dead handle |
| `rf_bridge` | socket established | no bytes since the device left |

The lesson is one sentence: **process liveness is not sample-flow health.**
A restart policy is a supervisor for processes that die. This one does not die,
so no restart policy can reach it, and the repair is a data-plane observation
rather than a stronger unit file.

### Detection

```bash
ss -ltnp '( sport = :1234 )'
lsusb -d 0bda:2838
curl -s http://127.0.0.1:5001/api/graphops/rf-bridge/status \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["bridge"]["capture_source"])'
```

The first two disagreeing — port bound, device absent — is the signature. The
third is the authoritative one: see §4.

### Recovery

```bash
systemctl --user restart scythe-rtl-tcp.service
```

After the USB device has been reattached from Windows, not before. Restarting
against an absent device just returns the unit to its ordinary retry loop.

Whether SCYTHE should issue that restart itself is **not decided here**. The
bridge observes the data plane; granting it authority to manage a systemd unit
is a separate decision with a separate review, and detection landing first is
deliberate rather than incomplete.

---

## 4. Transport and sample flow are separate axes

The failure above is only invisible if availability is read off the socket.
`rf_bridge` publishes two axes and derives the single word from the pair:

```json
{
  "transport_state": "CONNECTED",
  "sample_flow_state": "STARVED",
  "availability": "SOURCE_STARVED",
  "last_sample_age_ms": 4812,
  "last_frame_age_ms": 4960,
  "starvation_threshold_ms": 2500,
  "starvation_threshold_authority": "CONFIGURED_POLICY",
  "freshness_clock": "MONOTONIC"
}
```

| Transport | Sample flow | Availability |
| --- | --- | --- |
| Disconnected | None | `SOURCE_DISCONNECTED` |
| Connecting | None | `SOURCE_CONNECTING` |
| Connected | Active | `SOURCE_STREAMING` |
| Connected | No recent samples | `SOURCE_STARVED` |
| Connected | Samples but no complete FFT | `FRAME_ASSEMBLY_STARVED` |

The sample-flow clock counts **decoded samples, not received bytes.** `rtl_tcp`
answers every reconnect with a 12-byte `RTL0` greeting and, wedged, then sends
nothing. A byte clock resets on that greeting and reports `SOURCE_STREAMING`
for the next 2.5 s, so a flapping wedge oscillates between streaming and
starved forever. Twelve bytes of header is not sample flow.

Freshness is measured with `time.monotonic()`. This host takes wall-clock
steps — one of about 23½ hours was observed on 2026-09-06, which retroactively
re-rendered every `dmesg --ctime` line in the buffer — and a step against wall
time would make a healthy stream look starved or a dead one look fresh. UTC is
published only as display metadata.

`starvation_threshold_ms` defaults to 2500 and is set by
`SDRPP_STARVATION_THRESHOLD_MS`. It is **policy, not a derivation**. A threshold
computed from the sample rate would say a 2.048 MS/s stream must deliver bytes
every few milliseconds — true of the hardware, false of the host, where
scheduler stalls, WSL suspension and the 1 s receive timeout all produce
legitimate silence. The floor is 1000 ms because the receive loop wakes once a
second and cannot resolve anything finer; a smaller value is refused at
validation rather than accepted and quietly rounded.

### What happens while starved

* the IQ ring is invalidated once, for `SOURCE_STARVED`, on the **opening edge**
  of the incident — not once per check, which would push every other cause out
  of a bounded invalidation history with one event;
* no complete window can be issued, because the ring holds nothing;
* nothing is classified, because no frame arrives to classify;
* `/api/graphops/rf-spectrum/latest` still serves the last frame, marked
  `stale: true` with the availability as `stale_reason`. It was measured and it
  is not wrong; it has stopped describing now, and withholding it would replace
  a stale measurement with no measurement;
* the browser stops advancing waterfall rows and names the reason in the
  headline (`STALE // SOURCE_STARVED`) rather than only dimming;
* the bridge's ordinary reconnect strategy continues to run untouched.

---

## 5. Where raw IQ actually goes

An unqualified `raw_iq_exposed: false` was broader than the truth. `rtl_tcp` and
the orchestrator are two processes, and samples cross a real, unauthenticated
TCP socket to get from one to the other. The claim worth making is about
destinations, and the transport is published beside it rather than folded into a
denial that it exists:

```json
{
  "raw_iq_api_exposed": false,
  "raw_iq_browser_exposed": false,
  "raw_iq_cloud_exposed": false,
  "raw_iq_model_context_exposed": false,
  "raw_iq_persisted": false,
  "raw_iq_local_transport": "LOOPBACK_TCP",
  "raw_iq_listener": "127.0.0.1:1234"
}
```

Published as `raw_iq_exposure` in the retention block. The loopback bind in §6
is what makes `LOOPBACK_TCP` an acceptable answer rather than a confession — so
the two must be read together, and a reader can check the control instead of
trusting the word.

Per-payload `raw_iq_exposed: false` fields elsewhere are unchanged and stay
correct: they are claims that *that* bounded product contains no samples, which
is a narrower statement than one about the pipeline.

---

## 6. Loopback binding is a security control

`rtl_tcp` has **no authentication** and exposes both raw IQ and receiver
control. It must bind loopback only:

```bash
ss -ltnp '( sport = :1234 )'
```

Required:

```text
127.0.0.1:1234
```

Not acceptable — `0.0.0.0:1234` or `[::]:1234`. On those, "raw IQ is not
exposed" stops being true the moment anything can reach the WSL virtual
interface. A firewall is not a substitute for the bind address; `-a 127.0.0.1`
is the control.

---

## 7. The sample rate is configured, not attested

`rf_bridge` sends `rtl_tcp` only `SET_GAIN_MODE` (0x03) and `SET_GAIN` (0x04).
There is **no** `SET_SAMPLE_RATE` opcode, and `rtl_tcp` never acknowledges the
rate the tuner applied — the `RTL0` header carries a tuner type and gain count,
not a rate.

So `rtl_tcp -s` is the sole authority for what the hardware runs, while
`SDRPP_SAMPLE_RATE_HZ` is only what the bridge *claims*. The claim drives
`bin_width = sample_rate_hz / fft_size` and the frequency axis. A configured
rate and a confirmed rate produce identical-looking spectra, so the evidence
must distinguish them:

```json
{
  "sample_rate_hz": 2048000,
  "sample_rate_authority": "SHARED_LAUNCH_CONFIGURATION",
  "runtime_attestation": "UNAVAILABLE",
  "native_bin_width_hz": 500.0
}
```

Published as `capture_rate_declaration` in the bridge status.

Both units read the rate from **one** file, `~/.config/scythe/rf-capture.env`,
so the two cannot drift. `EnvironmentFile=` carries no leading `-`: a missing
file makes the orchestrator refuse to start rather than silently fall back to
`rf_bridge`'s 1 MS/s default and mislabel every frequency.

Single-sourcing removes configuration drift. It does **not** upgrade the
authority — the rate remains configured, never measured.

### Future work: a capture handshake record

To reach `LAUNCH_CONFIG_CORROBORATED` (still not `USB_MEASURED`), record at
connection time:

* environment-file hash;
* actual `rtl_tcp` command line (`/proc/<pid>/cmdline`);
* process start time and PID;
* server connection epoch;
* requested sample rate;
* any startup log line stating the applied rate;
* authority `LAUNCH_CONFIG_CORROBORATED`.

Do **not** estimate the rate from a known broadcast station. That replaces
configuration trust with transmitter trust and calls the swap a measurement.

---

## 8. Install (Linux)

```bash
scripts/install_rtl_tcp_user_service.sh
```

Then edit `~/.config/scythe/rf-capture.env` and set
`SCYTHE_RTL_DEVICE_SERIAL`. Find the serial with:

```bash
rtl_test -t 2>&1 | grep -i 'SN:'
```

The serial is **configuration authority, not USB attestation**: it selects
which receiver `rtl_tcp` opens. It does not prove which receiver is physically
connected or which antenna is on it. Device index `0` is avoided because index
is whichever SDR enumerates first, which need not be the receiver named in
`SDRPP_SENSOR_ID`.

### The antenna declaration does not persist itself

`SDRPP_ANTENNA_ID`, `SDRPP_FEEDLINE_ID` and `SDRPP_ANTENNA_EXTENSION_MM` in
`scythe-orchestrator.service.d/sdrpp.conf` are the **only** statement of the
signal chain that survives a restart. The declaration API store is
process-local and volatile: declaring in the browser does not write there, and
the status block says so —

```json
{
  "boot_declaration": "PENDING_PERSISTENCE",
  "runtime_declaration": "ACTIVE",
  "detail": "THIS DECLARATION IS ACTIVE IN THIS PROCESS ONLY. THE BOOT ENVIRONMENT
             STILL DECLARES A DIFFERENT INSTRUMENT AND A RESTART WOULD ADOPT IT"
}
```

A restart with `PENDING_PERSISTENCE` standing is an **evidentiary rollback**:
the extension reverts to `UNDECLARED`, the signal-chain hash reverts with it,
and products taken either side of the restart silently claim to share an
instrument they do not. Copy the runtime declaration into the drop-in before
restarting, then confirm `boot_declaration: PERSISTED`.

Two hashes move differently across that copy, and both behaviours are correct:

| Hash | Fields | Across a persist-and-restart |
| --- | --- | --- |
| `instrument_hash` | `antenna_id`, `feedline_id`, `extension_mm` | **unchanged** — products stay comparable |
| `declaration_hash` | those plus `authority`, `extension_authority`, `note` | changes — the boot record carries a different `note` |

Verified on 2026-09-06 across a full WSL restart: `extension_mm 368.0`,
`signal_chain_hash blake2s:bbfa48d4…` and `instrument_hash 537fd548…` all
identical before and after, with the declaration hash moving on the note alone.

User services only start at boot when lingering is enabled:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" | grep Linger    # expect Linger=yes
```

### Verification

```bash
systemctl --user status scythe-rtl-tcp.service
ss -ltnp '( sport = :1234 )'
curl -s localhost:5001/api/graphops/rf-bridge/status | python3 -m json.tool | head -40
```

Expect `bridge_state: streaming`, `capture_source.availability:
SOURCE_STREAMING`, and `latest_sequence` advancing between two calls.

`iq_connected: true` alone is **not** sufficient, and §3 is why: it reports the
socket, and the socket outlives the receiver.

---

## 9. Windows: usbipd auto-attach

> **Status: DOCUMENTED, NOT ACTIVATED.** No scheduled task has been created.
> Everything below is a procedure to run deliberately, not something installed.

Under WSL the receiver does not exist in Linux until Windows attaches it.
systemd inside WSL cannot trigger that. This is the one remaining manual step
after a reboot; everything after it is automatic within ~5s.

### Observed environment

| Fact | Value | Stability |
| --- | --- | --- |
| usbipd-win | 5.3.0 | — |
| WSL distribution | `AlmaLinux-10` | stable |
| Device | `0bda:2838` Realtek RTL2838 (NESDR SMArt v5) | **persistent** |
| Bus ID | `2-1` | **not permanent** — changes on replug/port change |
| Receiver serial | `14530058` | persistent, for post-attach verification |
| Windows account | `SCYTHE\benja`, not elevated | — |

Prefer `--hardware-id 0bda:2838` over `--busid 2-1`. The bus ID is an
observation of where the device sits today, not its identity.

### Step 1 — one-time binding (requires Administrator)

In an **elevated** PowerShell:

```powershell
usbipd bind --hardware-id 0bda:2838 --force
```

`--force` is required on this host: `usbipd list` reports the USBPcap filter
installed, and warns it is incompatible without forced binding. `--force` makes
the device unavailable to Windows itself while bound.

Binding persists across reboots. It is done once, not at each logon.

Verify:

```powershell
usbipd list
```

Expect `2-1  0bda:2838  NESDR SMArt v5  Shared` (or `Attached` once attached).

### Step 2 — attach

Attaching does **not** require elevation; `usbipd list` already succeeds as
`SCYTHE\benja` unelevated. Confirm on first run and adjust the task identity if
your host disagrees.

Manual, one shot:

```powershell
usbipd attach --wsl AlmaLinux-10 --hardware-id 0bda:2838
```

Persistent, re-attaching whenever the device is detached or unplugged:

```powershell
usbipd attach --wsl AlmaLinux-10 --hardware-id 0bda:2838 --auto-attach
```

`--auto-attach` runs in the **foreground indefinitely**. It is a long-running
process, not a command that returns — which is why it belongs in a logon task
rather than a startup script that expects an exit.

#### Selector trade-off (read before choosing)

`usbipd attach` accepts `--unplugged`, which allows auto-attaching a device that
is *currently absent* — but `--unplugged` **requires `--busid`** and cannot be
combined with `--hardware-id`.

| Variant | Survives bus change | Waits for an absent device |
| --- | :---: | :---: |
| `--hardware-id 0bda:2838 --auto-attach` | yes | **no** |
| `--busid 2-1 --unplugged --auto-attach` | **no** | yes |

Choose by habit: if the dongle is normally plugged in at logon, take the
hardware-id form. If it is normally plugged in *after* logon, the busid form is
the only one that waits — and its bus ID must be re-checked after any replug.

### Step 3 — logon task (create deliberately)

Run as the interactive user (`SCYTHE\benja`), at logon, without elevation. In an
elevated PowerShell:

```powershell
$action  = New-ScheduledTaskAction -Execute 'usbipd.exe' `
    -Argument 'attach --wsl AlmaLinux-10 --hardware-id 0bda:2838 --auto-attach'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'SCYTHE\benja'
$principal = New-ScheduledTaskPrincipal -UserId 'SCYTHE\benja' `
    -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0 `
    -RestartCount 6 -RestartInterval (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName 'SCYTHE-usbipd-autoattach' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings
```

`-ExecutionTimeLimit 0` is required: `--auto-attach` never exits, and the
default 3-day limit would kill it.

`-RestartCount 6 -RestartInterval 2min` softens the hardware-id trade-off in
the table above. It does not make `--hardware-id` wait for an absent device —
nothing does — but a task that failed because the dongle was not yet plugged in
will retry six times over twelve minutes, which covers the ordinary case of
plugging in shortly after logon. Beyond that window, plug in and run
`Start-ScheduledTask` by hand. This keeps the persistent `0bda:2838` selector
instead of canonising a bus ID that changes on replug.

### Negative case — device absent

With `--hardware-id` and the dongle unplugged, attach fails immediately rather
than waiting:

```text
usbipd: error: No device found with hardware-id '0bda:2838'.
```

The task then exits non-zero. With the `-RestartCount` setting above it retries
six times at two-minute intervals; after that, plug the dongle in and re-run the
attach command, or start the task manually with
`Start-ScheduledTask -TaskName 'SCYTHE-usbipd-autoattach'`.

On the Linux side nothing breaks — `scythe-rtl-tcp.service` is still retrying
every 5s and will come up on its own once the device appears.

### Verification, from both sides

Windows:

```powershell
usbipd list                                    # expect STATE = Attached
Get-ScheduledTask -TaskName 'SCYTHE-usbipd-autoattach' | Get-ScheduledTaskInfo
```

AlmaLinux:

```bash
lsusb | grep -i realtek                        # expect 0bda:2838
rtl_test -t 2>&1 | grep -i 'SN:'               # expect SN: 14530058
systemctl --user status scythe-rtl-tcp.service # expect active (running)
```

The `lsusb` line proves *a* matching device arrived. The serial check is what
ties it to the receiver named in `SDRPP_SENSOR_ID` — matching VID:PID alone
would be satisfied by any RTL2838.

### Rollback / removal

```powershell
# Stop and remove the logon task
Stop-ScheduledTask   -TaskName 'SCYTHE-usbipd-autoattach'
Unregister-ScheduledTask -TaskName 'SCYTHE-usbipd-autoattach' -Confirm:$false

# Detach (returns the device to WSL-less state; keeps the binding)
usbipd detach --hardware-id 0bda:2838

# Unbind, returning the device to Windows (elevated)
usbipd unbind --hardware-id 0bda:2838
```

Linux side:

```bash
systemctl --user disable --now scythe-rtl-tcp.service
rm ~/.config/systemd/user/scythe-rtl-tcp.service
rm ~/.config/systemd/user/scythe-orchestrator.service.d/rf-capture.conf
systemctl --user daemon-reload
```

Removing `rf-capture.conf` without restoring the four `Environment=` lines will
leave the orchestrator refusing to start, which is the intended fail-loud
behaviour rather than a silent fallback to 1 MS/s.

---

## 10. Troubleshooting

| Symptom | Check |
| --- | --- |
| `NO FRAME RETAINED` | `systemctl --user status scythe-rtl-tcp.service`; then `lsusb \| grep -i realtek` |
| Unit `active (running)`, `NRestarts=0`, no frames | The §3 wedge. `lsusb -d 0bda:2838`; reattach, then restart the unit |
| `availability: SOURCE_STARVED` | Socket up, no bytes. §3 detection, then §3 recovery |
| `availability: FRAME_ASSEMBLY_STARVED` | Samples arriving, no complete FFT — a decode or sizing fault, not a USB one |
| Unit `activating (auto-restart)` forever | Device not attached from Windows — see §9 |
| Unit `failed` immediately | Another `rtl_tcp` holds port 1234 or the device: `ss -ltnp '( sport = :1234 )'` |
| Wrong frequency labels | `capture_rate_declaration.sample_rate_hz` vs the unit's `-s`; both come from `rf-capture.env` |
| Orchestrator will not start | `~/.config/scythe/rf-capture.env` missing — deliberate, see §7 |

### `systemctl --user` refuses to connect

```text
Failed to connect to user scope bus via local transport: Connection refused
```

Seen on this host: `/run/user/1000/systemd/private` refused while
`/run/user/1000/bus` accepted. The manager is healthy; the private transport is
wedged. It clears on reboot. In the meantime systemd can be driven over D-Bus:

```bash
U=/org/freedesktop/systemd1
busctl --user call org.freedesktop.systemd1 $U org.freedesktop.systemd1.Manager Reload
busctl --user call org.freedesktop.systemd1 $U org.freedesktop.systemd1.Manager \
    StartUnit ss "scythe-rtl-tcp.service" "replace"
busctl --user get-property org.freedesktop.systemd1 \
    $U/unit/scythe_2drtl_2dtcp_2eservice org.freedesktop.systemd1.Unit ActiveState
```
