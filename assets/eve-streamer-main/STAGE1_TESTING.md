# Stage 1 AF_PACKET Testing & Benchmarking

This document outlines how to validate and benchmark the AF_PACKET zero-copy capture mode against the legacy Suricata eve.json pipeline.

---

## 🧪 Prerequisites

### System Requirements

- Linux kernel 3.2+ (TPACKET_V3 support)
- Go 1.21+
- `tcpreplay` (for synthetic traffic; `apt install tcpreplay`)
- Sample PCAP files (e.g., from `assets/artifacts/pcap/`)
- Root or `CAP_NET_RAW` capability

### Prepare Environment

```bash
cd assets/eve-streamer

# Grant capability (safer than root)
sudo setcap cap_net_raw+ep ./bin/eve-streamer

# Verify
getcap ./bin/eve-streamer
# Output: ./bin/eve-streamer = cap_net_raw+ep

# Or run with root (for initial testing)
sudo su
```

---

## 📊 Benchmark: Suricata vs. AF_PACKET

### Test 1: Memory Footprint

**Suricata Mode**

```bash
# Terminal 1: Start Suricata mode
./bin/eve-streamer -mode suricata -eve /tmp/test-events.json -port :50051 &
eve_streamer_pid=$!

# Terminal 2: Monitor RSS
watch -n 1 'ps aux | grep eve-streamer | grep -v grep | awk "{print \$6 \" KB\"}"'

# After 30 seconds—note the RSS value
# Expected: 30–50 MB
```

**AF_PACKET Mode**

```bash
# Generate traffic on test interface (e.g., lo or veth pair)
# Terminal 1: Start AF_PACKET mode
sudo ./bin/eve-streamer -mode afpacket -iface lo -port :50051 &
eve_streamer_pid=$!

# Terminal 2: Monitor RSS
watch -n 1 'ps aux | grep eve-streamer | grep -v grep | awk "{print \$6 \" KB\"}"'

# Expected: 20–40 MB (mmap overhead is fixed; no dynamic allocation)
```

### Test 2: Latency (End-to-End)

**Setup Synchronization**

Add tight timestamping to both paths:

```bash
# Suricata: log entry timestamp (already in eve.json)
# AF_PACKET: capture timestamp in packet header (use kernel time)

# Time from capture to gRPC send (measure in ns)
```

Using the gRPC batch format:

```go
// Both modes emit EventBatch with event timestamps
// Compare: (event.Timestamp - capture_time) = ingest latency
```

**Expected Results**

| Mode | Latency (p50) | Latency (p99) |
|------|---------------|---------------|
| Suricata | 5–20ms | 50–200ms (GC/disk) |
| AF_PACKET | <100μs | <500μs |

### Test 3: CPU Usage Under Load

**Generate Synthetic Traffic**

```bash
# Replay a PCAP with tcpreplay
# Use a moderate traffic PCAP from assets/artifacts/pcap/

pcap_file="assets/artifacts/pcap/<hash>.pcap"

# Check available interfaces
ip link

# Create virtual interface (optional; for isolated testing)
sudo ip link add veth0 type veth peer name veth1

# Terminal 1: Start AF_PACKET mode
sudo ./bin/eve-streamer -mode afpacket -iface veth0 -batch-size 500 -port :50051

# Terminal 2: Replay traffic
sudo tcpreplay --intf1=veth0 "$pcap_file" --loop=100 --mbps=500

# Terminal 3: Monitor CPU
top -p $eve_streamer_pid
# Watch: %CPU column
# Expected: 10–25% on a single core for moderate replay
```

### Test 4: Throughput (Events/sec)

**Uniform Traffic**

```bash
# Measure gRPC batch emission rate

# Terminal 1: AF_PACKET mode (with logging)
sudo ./bin/eve-streamer \
  -mode afpacket \
  -iface veth0 \
  -batch-size 100 \
  -batch-time 100ms \
  -port :50051 \
  2>&1 | tee /tmp/afpacket.log

# Terminal 2: Replay PCAP at high rate
sudo tcpreplay --intf1=veth0 "$pcap_file" --loop=1000 --mbps=1000

# Terminal 3: Count batches
tail -f /tmp/afpacket.log | grep "Sending batch" | wc -l
# Or post-hoc:
grep "Sending batch" /tmp/afpacket.log | wc -l
```

**Expected Results**

- AF_PACKET: 1–5 million packets/sec per core (depends on CPU)
- Suricata: 100k–500k events/sec (IDS overhead)

---

## 🔍 Validation Checklist

- [ ] Compile without errors (`make build`)
- [ ] Help text shows new flags (`./bin/eve-streamer -h` includes `-mode`, `-iface`)
- [ ] Suricata mode still works (`-mode suricata`)
- [ ] AF_PACKET mode initializes (no permission errors with CAP_NET_RAW)
- [ ] AF_PACKET mode logs ring initialization
- [ ] Batches are created and logged
- [ ] Remote streaming works (`-remote` flag accepted in both modes)
- [ ] SIGINT/SIGTERM gracefully shuts down both modes
- [ ] Memory usage stable over time (no leaks)

---

## 🐛 Troubleshooting

### Error: `Operation not permitted` (AF_PACKET mode)

```
Failed to open AF_PACKET socket: operation not permitted
```

**Solution:** Ensure capability or root:

```bash
sudo setcap cap_net_raw+ep ./bin/eve-streamer
# or
sudo chmod +s ./bin/eve-streamer
```

### Error: `Unknown interface`

```
Failed to lookup interface lo: ...
```

**Solution:** Verify interface exists:

```bash
ip link show
# Use a real interface (e.g., eth0, veth0) not lo if lo not available
```

### Error: `Failed to set PACKET_VERSION`

```
Failed to set PACKET_VERSION: operation not supported
```

**Solution:** Kernel doesn't support TPACKET_V3; upgrade to Linux 3.2+.

---

## 📈 Benchmark Results Template

Record results for comparison:

```markdown
## AF_PACKET Stage 1 Benchmark Results

**System:** [CPU, RAM, kernel version]
**PCAP:** [filename, packet count, size]

### Memory
- Suricata RSS: __ MB
- AF_PACKET RSS: __ MB

### Latency (ingest → batch send)
- Suricata p50: __ ms
- AF_PACKET p50: __ µs

### CPU @ 500 Mbps replay
- Suricata: __%
- AF_PACKET: __%

### Throughput
- AF_PACKET: __ packets/sec
- Batch rate: __ batches/sec

### Notes
[Any observations, bottlenecks, or tuning applied]
```

---

## 🎯 Next Steps (Stage 2)

Once AF_PACKET is validated:

1. **Flow Tracker:** Aggregate 5-tuples into sessions
2. **Feature Extractor:** Extract packet-level features (entropy, timing, directionality)
3. **Embedding Model:** Compress features into 32-byte vector
4. **FlatBuffers Encoding:** Replace protobuf for zero-copy serialization

Each stage inherits the AF_PACKET foundation without modification.

