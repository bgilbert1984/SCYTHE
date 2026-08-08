# Eve-Streamer: Go gRPC Daemon for Suricata Events

A high-performance Go daemon that reads Suricata's eve.json, batches events, and streams them over gRPC.

## Features

- **Event Reading**: Continuously reads from eve.json (tails new events)
- **Event Batching**: Configurable batch size and time window
- **Event Normalization**: Extracts entities and relationships from raw events
- **gRPC Streaming**: Efficient proto3-based streaming
- **Graceful Shutdown**: Handles SIGINT/SIGTERM

## Building

### Prerequisites
- Go 1.21+
- Protocol Buffers compiler (`protoc`)
- Clang/LLVM (for eBPF mode)

### Build Commands

```bash
# Generate protobuf code and build
make build

# Just generate protos
make proto

# Build BPF object
make bpf

# Run the daemon
make run

# Clean build artifacts
make clean
```

## Usage

```bash
./eve-streamer \
  -port :50051 \
  -eve /var/log/suricata/eve.json \
  -batch-size 100 \
  -batch-time 5s \
  [-remote hypergraph-host:50051]   # shipper/client mode
```

### Advanced Capture Modes

#### TPACKET_V3 (Zero-Copy)
High-performance zero-copy capture using memory-mapped ring buffers and kernel-level notifications (`select/poll`).

```bash
sudo ./eve-streamer \
  -mode tpacket_v3 \
  -iface eth0
```

#### eBPF (XDP) — Stage 2A Flow Tracking
Extreme-performance in-kernel flow aggregation. The XDP program parses headers, tracks 5-tuple flows in an LRU map, and only emits structured flow updates to userspace.
- **Volume Reduction**: Orders of magnitude less data sent to Go.
- **TCP Awareness**: Tracks TCP flags (SYN, FIN, RST) and emits `flow_end` events.
- **Metrics**: Real-time packet and byte counts per flow.

```bash
sudo ./eve-streamer \
  -mode ebpf \
  -iface eth0
```

### CaptureEngineFactory & Intent-Based Selection

The system now supports **Requirement Negotiation**. Instead of selecting a specific mode, higher-level components can request engines based on intent:

```go
engine, err := factory.CreateWithRequirements(EngineRequirements{
    MustEmitFlows:  true,
    MustBeZeroCopy: true,
}, cfg)
```

### Telemetry & Observability

Ingestion health is now a first-class citizen. Each engine exports real-time metrics via an HTTP endpoint (default port `:8081`):

```bash
curl http://localhost:8081/capture/metrics
```

**Exported Metrics:**
- `packets_processed`: Raw packet count seen by the kernel.
- `events_emitted`: Structured events sent to the hypergraph.
- `bytes_processed`: Total throughput.
- `ring_utilization`: (TPACKET only) Buffer health.

## Architecture

### Event Normalization
Each raw Suricata event is transformed into:
- **event_id**: UUID
- **type**: From `event_type` field
- **entities**: Extracted key-value pairs (src_ip, dest_ip, ports, protocol, etc.)
- **edges**: Network flow relationships (src -> dest)
- **timestamp**: Original event timestamp

### Batching Strategy
Events are batched using a "time or count" strategy:
- Send when batch reaches `batch-size` events, OR
- Send when `batch-time` duration expires

This ensures low latency while maintaining efficiency.

---

## 🚀 Stage 1: AF_PACKET Zero-Copy Capture Mode (Production-Ready)

**The Major Inflection Point:** Replace Suricata + eve.json file tailing with **direct kernel packet capture** via `AF_PACKET` TPACKET_V3.

### What Changed

Previous pipeline (wasteful):
```
NIC → Kernel → Suricata → eve.json (disk) → JSON parse → Go → gRPC
```

New pipeline (deterministic):
```
NIC → Kernel RX ring (mmap) → Go extracts 5-tuple → gRPC
```

**Elimination:**
- ✅ Disk I/O
- ✅ JSON parsing overhead
- ✅ File tailing complexity
- ✅ Suricata runtime dependency
- ✅ Allocation churn

### Usage: AF_PACKET Mode

```bash
# Requires root or CAP_NET_RAW capability
sudo ./eve-streamer \
  -mode afpacket \
  -iface eth0 \
  -port :50051 \
  -batch-size 100 \
  -batch-time 5s \
  [-remote hypergraph-host:50051]
```

### Performance Profile (Typical Moderate Traffic)

| Metric | Value |
|--------|-------|
| RSS memory | 20–40 MB |
| CPU usage | <1 core (several hundred kpps) |
| Ingest latency | Microseconds (before gRPC) |
| GC pause | Sub-millisecond |
| Syscalls per packet | 0 (zero-copy) |

---

## Docker

```bash
# Build container
make docker-build

# Run in Docker
docker run -v /var/log/suricata:/var/log/suricata eve-streamer
```

Special Thanks to Raptor-mini AI Agent within VSCode for the strong start on eve-streamer, Gemini, ChatGPT and Claude also helped.

Arapt.us | Ben Gilbert
