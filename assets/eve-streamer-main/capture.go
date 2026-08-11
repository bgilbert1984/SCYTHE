package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"github.com/cilium/ebpf"
	"github.com/cilium/ebpf/link"
	"github.com/cilium/ebpf/ringbuf"
	flatbuffers "github.com/google/flatbuffers/go"
	"github.com/google/uuid"
	rfscythe "github.com/yourorg/eve-streamer/fb/rfscythe"
	pb "github.com/yourorg/eve-streamer/pb"
)

// BaseEngine provides shared metrics tracking for all capture engines.
type BaseEngine struct {
	packetsProcessed uint64
	eventsEmitted    uint64
	bytesProcessed   uint64
	dropped          uint64
}

func (b *BaseEngine) Metrics() EngineMetrics {
	return EngineMetrics{
		PacketsProcessed: atomic.LoadUint64(&b.packetsProcessed),
		EventsEmitted:    atomic.LoadUint64(&b.eventsEmitted),
		BytesProcessed:   atomic.LoadUint64(&b.bytesProcessed),
		Dropped:          atomic.LoadUint64(&b.dropped),
	}
}

func (b *BaseEngine) countPacket(n int) {
	atomic.AddUint64(&b.packetsProcessed, 1)
	atomic.AddUint64(&b.bytesProcessed, uint64(n))
}

func (b *BaseEngine) countEvent() {
	atomic.AddUint64(&b.eventsEmitted, 1)
}

// ---------------------------------------------------------------------------
// Suricata (file tail) implementation
// ---------------------------------------------------------------------------

type SuricataEngine struct {
	BaseEngine
	FilePath   string
	ReplayLast int
}

func NewSuricataEngine(cfg EngineConfig) CaptureEngine {
	return &SuricataEngine{FilePath: cfg.EveFile, ReplayLast: cfg.ReplayLast}
}

func init() {
	RegisterEngine("suricata", NewSuricataEngine)
}

func (e *SuricataEngine) Name() string {
	return "suricata"
}

func (e *SuricataEngine) Capabilities() EngineCapabilities {
	return EngineCapabilities{
		ZeroCopy:        false,
		KernelOffload:   false,
		EmitsRawPackets: false,
		EmitsFlows:      true,
	}
}

func (e *SuricataEngine) Validate() error {
	if _, err := resolveEvePath(e.FilePath); err != nil {
		return err
	}
	return nil
}

const maxReplayScanBytes int64 = 16 * 1024 * 1024

// seekForRecentLines positions file at the start of at most count complete or
// pending newline-delimited records. The byte scan is bounded independently of
// the Eve file size.
func seekForRecentLines(file *os.File, count int) (int, error) {
	info, err := file.Stat()
	if err != nil {
		return 0, err
	}
	if count <= 0 || info.Size() == 0 {
		_, err = file.Seek(0, io.SeekEnd)
		return 0, err
	}
	scanStart := info.Size() - maxReplayScanBytes
	if scanStart < 0 {
		scanStart = 0
	}
	data := make([]byte, info.Size()-scanStart)
	read, readErr := file.ReadAt(data, scanStart)
	if readErr != nil && readErr != io.EOF {
		return 0, readErr
	}
	data = data[:read]
	offset := scanStart
	index := len(data) - 1
	if index >= 0 && data[index] == '\n' {
		index--
	}
	boundaries := 0
	for ; index >= 0; index-- {
		if data[index] == '\n' {
			boundaries++
			if boundaries == count {
				offset = scanStart + int64(index) + 1
				break
			}
		}
	}
	if boundaries < count && scanStart > 0 {
		// The bounded scan began inside a record. Never emit that fragment.
		if firstNewline := bytes.IndexByte(data, '\n'); firstNewline >= 0 {
			offset = scanStart + int64(firstNewline) + 1
		} else {
			offset = info.Size()
		}
	}
	if _, err := file.Seek(offset, io.SeekStart); err != nil {
		return 0, err
	}
	segment := data[int(offset-scanStart):]
	records := bytes.Count(segment, []byte{'\n'})
	if len(segment) > 0 && segment[len(segment)-1] != '\n' {
		records++
	}
	return records, nil
}

func evePathIsPattern(path string) bool {
	return strings.ContainsAny(path, "*?[")
}

func resolveEvePath(path string) (string, error) {
	if !evePathIsPattern(path) {
		if _, err := os.Stat(path); err != nil {
			return "", fmt.Errorf("suricata eve.json not found: %s", path)
		}
		return path, nil
	}
	matches, err := filepath.Glob(path)
	if err != nil {
		return "", fmt.Errorf("invalid suricata eve.json pattern %q: %w", path, err)
	}
	type candidate struct {
		path string
		info os.FileInfo
	}
	files := make([]candidate, 0, len(matches))
	for _, match := range matches {
		info, statErr := os.Stat(match)
		if statErr == nil && info.Mode().IsRegular() {
			files = append(files, candidate{path: match, info: info})
		}
	}
	if len(files) == 0 {
		return "", fmt.Errorf("suricata eve.json pattern has no files: %s", path)
	}
	sort.Slice(files, func(i, j int) bool {
		if files[i].info.ModTime().Equal(files[j].info.ModTime()) {
			return files[i].path < files[j].path
		}
		return files[i].info.ModTime().Before(files[j].info.ModTime())
	})
	return files[len(files)-1].path, nil
}

func (e *SuricataEngine) Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error {
	activePath, err := resolveEvePath(e.FilePath)
	if err != nil {
		return err
	}
	file, err := os.Open(activePath)
	if err != nil {
		return fmt.Errorf("failed to open eve.json: %w", err)
	}
	defer file.Close()

	replayRemaining, err := seekForRecentLines(file, e.ReplayLast)
	if err != nil {
		return fmt.Errorf("failed to seek eve.json: %w", err)
	}
	if replayRemaining > 0 {
		log.Printf("Bootstrapping %d recent eve.json records before live tail", replayRemaining)
	}
	reader := bufio.NewReaderSize(file, 64*1024)
	var pending []byte
	const maxEventBytes = 1024 * 1024

	for {
		select {
		case <-done:
			return nil
		default:
		}

		fragment, readErr := reader.ReadBytes('\n')
		pending = append(pending, fragment...)
		if len(pending) > maxEventBytes {
			pending = pending[:0]
			atomic.AddUint64(&e.dropped, 1)
			log.Printf("Dropped eve.json record larger than %d bytes", maxEventBytes)
		}
		if readErr == io.EOF {
			// A Scanner becomes permanently exhausted after its first EOF.  A
			// Reader can be retried, which is required for a true append-only tail.
			// Also reopen after log rotation when the path names a new inode.
			candidatePath, resolveErr := resolveEvePath(e.FilePath)
			if resolveErr != nil {
				time.Sleep(100 * time.Millisecond)
				continue
			}
			pathInfo, pathErr := os.Stat(candidatePath)
			fileInfo, fileErr := file.Stat()
			if pathErr == nil && fileErr == nil && (candidatePath != activePath || !os.SameFile(pathInfo, fileInfo)) {
				replacement, openErr := os.Open(candidatePath)
				if openErr != nil {
					return fmt.Errorf("failed to reopen rotated eve.json: %w", openErr)
				}
				file.Close()
				file = replacement
				activePath = candidatePath
				reader = bufio.NewReaderSize(file, 64*1024)
				pending = pending[:0]
			}
			time.Sleep(100 * time.Millisecond)
			continue
		}
		if readErr != nil {
			return fmt.Errorf("eve.json read error: %w", readErr)
		}
		if len(pending) == 0 {
			continue
		}
		isReplay := replayRemaining > 0
		if isReplay {
			replayRemaining--
		}

		e.countPacket(len(pending))

		var raw map[string]interface{}
		if err := json.Unmarshal(pending, &raw); err != nil {
			log.Printf("Failed to parse JSON: %v", err)
			pending = pending[:0]
			continue
		}
		pending = pending[:0]

		event := normalizeEvent(raw)
		if isReplay {
			event.Entities = append(event.Entities, &pb.Entity{Key: "scythe_ingest_mode", Value: "bootstrap_replay"})
			if replayRemaining == 0 {
				log.Printf("Recent eve.json bootstrap complete; continuing live tail")
			}
		}
		select {
		case eventCh <- event:
		case <-done:
			return nil
		}
		e.countEvent()
	}
}

// ---------------------------------------------------------------------------
// AF_PACKET "raw" capture implementation (simplified version)
// ---------------------------------------------------------------------------

type PacketEngine struct {
	BaseEngine
	Iface string
}

func NewPacketEngine(cfg EngineConfig) CaptureEngine {
	return &PacketEngine{Iface: cfg.Iface}
}

func init() {
	RegisterEngine("afpacket", NewPacketEngine)
}

func (e *PacketEngine) Name() string {
	return "afpacket"
}

func (e *PacketEngine) Capabilities() EngineCapabilities {
	return EngineCapabilities{
		ZeroCopy:          false,
		KernelOffload:     false,
		RequiresRoot:      true,
		EmitsRawPackets:   true,
		MaxThroughputMpps: 0.5,
	}
}

func (e *PacketEngine) Validate() error {
	return requireRoot()
}

func (e *PacketEngine) Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error {
	fd, err := syscall.Socket(syscall.AF_PACKET, syscall.SOCK_RAW, int(htons(syscall.ETH_P_ALL)))
	if err != nil {
		return fmt.Errorf("failed to open AF_PACKET socket: %w", err)
	}
	defer syscall.Close(fd)

	iface, err := net.InterfaceByName(e.Iface)
	if err != nil {
		return fmt.Errorf("failed to lookup interface %s: %w", e.Iface, err)
	}

	sll := &syscall.SockaddrLinklayer{
		Protocol: htons(syscall.ETH_P_ALL),
		Ifindex:  iface.Index,
	}
	if err := syscall.Bind(fd, sll); err != nil {
		return fmt.Errorf("failed to bind to interface: %w", err)
	}

	log.Printf("AF_PACKET initialized on %s (ifindex=%d)", e.Iface, iface.Index)

	// nonblocking read with small timeout so we can check `done`
	syscall.SetsockoptTimeval(fd, syscall.SOL_SOCKET, syscall.SO_RCVTIMEO, &syscall.Timeval{Sec: 0, Usec: 100000})

	buf := make([]byte, 65536)

	for {
		select {
		case <-done:
			return nil
		default:
		}

		n, _, err := syscall.Recvfrom(fd, buf, 0)
		if err != nil {
			if err == syscall.EAGAIN || err == syscall.EWOULDBLOCK {
				continue
			}
			return fmt.Errorf("recvfrom error: %w", err)
		}

		if n < 42 {
			continue
		}

		e.countPacket(n)

		srcIP, dstIP, srcPort, dstPort, proto, ok := extractPacketFromBuffer(buf[:n])
		if !ok {
			continue
		}

		// Optimized Binary Path (Stage 4)
		builder := builderPool.Get().(*flatbuffers.Builder)
		binMsg := packetToBinaryFlow(builder, srcIP, dstIP, srcPort, dstPort, proto, uint64(n), time.Now())
		binaryCh <- binMsg
		builderPool.Put(builder)

		// Legacy JSON path
		event := packetToEvent(srcIP, dstIP, srcPort, dstPort, proto, time.Now())
		eventCh <- event
		e.countEvent()
	}
}

// ---------------------------------------------------------------------------
// TPACKET_V3 Zero-Copy implementation
// ---------------------------------------------------------------------------

const (
	TPACKET_V3     = 2
	PACKET_VERSION = 10
	PACKET_RX_RING = 5
	TP_STATUS_USER = 1
)

type tpacket_req3 struct {
	tp_block_size       uint32
	tp_block_nr         uint32
	tp_frame_size       uint32
	tp_frame_nr         uint32
	tp_retire_blk_tov   uint32
	tp_sizeof_priv      uint32
	tp_feature_req_word uint32
}

type tpacket_block_desc struct {
	version        uint32
	offset_to_priv uint32
	h1             tpacket_hdr_v1
}

type tpacket_hdr_v1 struct {
	block_status        uint32
	num_pkts            uint32
	offset_to_first_pkt uint32
	blk_len             uint32
	seq_num             uint64
	ts_last_pkt_sec     uint32
	ts_last_pkt_nsec    uint32
	ts_first_pkt_sec    uint32
	ts_first_pkt_nsec   uint32
}

type tpacket3_hdr struct {
	tp_next_offset uint32
	tp_sec         uint32
	tp_nsec        uint32
	tp_snaplen     uint32
	tp_len         uint32
	tp_status      uint32
	tp_mac         uint16
	tp_net         uint16
}

type TPacketV3Engine struct {
	BaseEngine
	Iface      string
	BlockSize  int
	BlockCount int
	FrameSize  int
}

func NewTPacketV3Engine(cfg EngineConfig) CaptureEngine {
	return &TPacketV3Engine{
		Iface:      cfg.Iface,
		BlockSize:  cfg.BlockSize,
		BlockCount: cfg.BlockCount,
		FrameSize:  cfg.FrameSize,
	}
}

func init() {
	RegisterEngine("tpacket_v3", NewTPacketV3Engine)
}

func (e *TPacketV3Engine) Name() string {
	return "tpacket_v3"
}

func (e *TPacketV3Engine) Capabilities() EngineCapabilities {
	return EngineCapabilities{
		ZeroCopy:          true,
		KernelOffload:     false,
		RequiresRoot:      true,
		EmitsRawPackets:   true,
		MaxThroughputMpps: 2.5,
	}
}

func (e *TPacketV3Engine) Validate() error {
	return requireRoot()
}

func (e *TPacketV3Engine) Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error {
	fd, err := syscall.Socket(syscall.AF_PACKET, syscall.SOCK_RAW, int(htons(syscall.ETH_P_ALL)))
	if err != nil {
		return fmt.Errorf("failed to open AF_PACKET socket: %w", err)
	}
	defer syscall.Close(fd)

	// Set version to V3
	val := int(TPACKET_V3)
	err = syscall.SetsockoptInt(fd, syscall.SOL_PACKET, PACKET_VERSION, val)
	if err != nil {
		return fmt.Errorf("failed to set TPACKET_V3: %w", err)
	}

	// Configure RX_RING
	req := tpacket_req3{
		tp_block_size:     uint32(e.BlockSize),
		tp_block_nr:       uint32(e.BlockCount),
		tp_frame_size:     uint32(e.FrameSize),
		tp_frame_nr:       uint32((e.BlockSize * e.BlockCount) / e.FrameSize),
		tp_retire_blk_tov: 100, // timeout in ms
	}

	// We need to pass the raw bytes of tpacket_req3
	reqBytes := (*[unsafe.Sizeof(req)]byte)(unsafe.Pointer(&req))[:]
	_, _, errno := syscall.Syscall6(syscall.SYS_SETSOCKOPT, uintptr(fd), uintptr(syscall.SOL_PACKET), uintptr(PACKET_RX_RING), uintptr(unsafe.Pointer(&reqBytes[0])), uintptr(len(reqBytes)), 0)
	if errno != 0 {
		return fmt.Errorf("failed to set PACKET_RX_RING: %v", errno)
	}

	// mmap the ring
	ringLen := e.BlockSize * e.BlockCount
	data, err := syscall.Mmap(fd, 0, ringLen, syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	if err != nil {
		return fmt.Errorf("failed to mmap RX_RING: %w", err)
	}
	defer syscall.Munmap(data)

	iface, err := net.InterfaceByName(e.Iface)
	if err != nil {
		return fmt.Errorf("failed to lookup interface %s: %w", e.Iface, err)
	}

	sll := &syscall.SockaddrLinklayer{
		Protocol: htons(syscall.ETH_P_ALL),
		Ifindex:  iface.Index,
	}
	if err := syscall.Bind(fd, sll); err != nil {
		return fmt.Errorf("failed to bind to interface: %w", err)
	}

	log.Printf("TPACKET_V3 initialized on %s (ring=%d MB)", e.Iface, ringLen/1024/1024)

	currentBlock := 0
	for {
		select {
		case <-done:
			return nil
		default:
		}

		blockOffset := currentBlock * e.BlockSize
		blockDesc := (*tpacket_block_desc)(unsafe.Pointer(&data[blockOffset]))

		if blockDesc.h1.block_status&TP_STATUS_USER != 0 {
			// Process packets in block
			numPkts := blockDesc.h1.num_pkts
			pktOffset := uint32(blockDesc.h1.offset_to_first_pkt)

			for i := uint32(0); i < numPkts; i++ {
				hdr := (*tpacket3_hdr)(unsafe.Pointer(&data[blockOffset+int(pktOffset)]))

				// Extract packet data
				macOffset := uint32(hdr.tp_mac)
				pktData := data[blockOffset+int(pktOffset)+int(macOffset) : blockOffset+int(pktOffset)+int(macOffset)+int(hdr.tp_snaplen)]

				e.countPacket(len(pktData))

				srcIP, dstIP, srcPort, dstPort, proto, ok := extractPacketFromBuffer(pktData)
				if ok {
					// Optimized Binary Path (Stage 4)
					builder := builderPool.Get().(*flatbuffers.Builder)
					binMsg := packetToBinaryFlow(builder, srcIP, dstIP, srcPort, dstPort, proto, uint64(len(pktData)), time.Unix(int64(hdr.tp_sec), int64(hdr.tp_nsec)))
					binaryCh <- binMsg
					builderPool.Put(builder)

					// Legacy JSON path
					event := packetToEvent(srcIP, dstIP, srcPort, dstPort, proto, time.Unix(int64(hdr.tp_sec), int64(hdr.tp_nsec)))
					eventCh <- event
					e.countEvent()
				}

				pktOffset += hdr.tp_next_offset
			}

			// Mark block as free for kernel
			blockDesc.h1.block_status = 0
			currentBlock = (currentBlock + 1) % e.BlockCount
		} else {
			// Wait for data using poll/select for silicon-speed efficiency
			readFds := &syscall.FdSet{}
			readFds.Bits[fd/64] |= 1 << (uint(fd) % 64)
			timeout := &syscall.Timeval{Sec: 0, Usec: 100000} // 100ms
			_, err := syscall.Select(fd+1, readFds, nil, nil, timeout)
			if err != nil && err != syscall.EINTR {
				log.Printf("Select error: %v", err)
			}
		}
	}
}

// ---------------------------------------------------------------------------
// eBPF Capture implementation
// ---------------------------------------------------------------------------

// bpfFlowCore mirrors the kernel struct flow_core in bpf_capture.c.
// The layout must match rfscythe.FlowCore exactly (56 bytes, little-endian).
type bpfFlowCore struct {
	FlowId    uint64
	Ts        uint64
	SrcIP     uint32
	DstIP     uint32
	SrcPort   uint16
	DstPort   uint16
	Proto     uint8
	EventType uint8
	_pad      [2]byte
	Packets   uint64
	Bytes     uint64
	FlowHash  uint64
}

var builderPool = sync.Pool{
	New: func() interface{} {
		return flatbuffers.NewBuilder(512)
	},
}

type EBPFEngine struct {
	BaseEngine
	Iface string
}

func NewEBPFEngine(cfg EngineConfig) CaptureEngine {
	return &EBPFEngine{Iface: cfg.Iface}
}

func init() {
	RegisterEngine("ebpf", NewEBPFEngine)
}

func (e *EBPFEngine) Name() string {
	return "ebpf"
}

func (e *EBPFEngine) Capabilities() EngineCapabilities {
	return EngineCapabilities{
		ZeroCopy:          true,
		KernelOffload:     true,
		RequiresRoot:      true,
		RequiresXDP:       true,
		EmitsRawPackets:   false,
		EmitsFlows:        true,
		MaxThroughputMpps: 15.0,
	}
}

func (e *EBPFEngine) Validate() error {
	if err := requireRoot(); err != nil {
		return err
	}
	if _, err := os.Stat("/sys/fs/bpf"); os.IsNotExist(err) {
		return errors.New("bpffs not mounted")
	}
	return nil
}

func (e *EBPFEngine) Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error {
	// Load pre-compiled BPF object
	spec, err := ebpf.LoadCollectionSpec("bpf_capture.o")
	if err != nil {
		return fmt.Errorf("failed to load BPF spec: %w (run 'make bpf')", err)
	}

	var objs struct {
		XdpCapture *ebpf.Program `ebpf:"xdp_capture"`
		Rb         *ebpf.Map     `ebpf:"rb"`
	}

	if err := spec.LoadAndAssign(&objs, nil); err != nil {
		return fmt.Errorf("failed to load BPF objects: %w", err)
	}
	defer objs.XdpCapture.Close()
	defer objs.Rb.Close()

	iface, err := net.InterfaceByName(e.Iface)
	if err != nil {
		return fmt.Errorf("failed to find interface %s: %w", e.Iface, err)
	}

	l, err := link.AttachXDP(link.XDPOptions{
		Program:   objs.XdpCapture,
		Interface: iface.Index,
	})
	if err != nil {
		return fmt.Errorf("failed to attach XDP: %w", err)
	}
	defer l.Close()

	rd, err := ringbuf.NewReader(objs.Rb)
	if err != nil {
		return fmt.Errorf("failed to create ringbuf reader: %w", err)
	}
	defer rd.Close()

	go func() {
		<-done
		rd.Close()
	}()

	log.Printf("eBPF XDP flow engine attached to %s", e.Iface)

	const fcSize = rfscythe.FlowCoreSize
	for {
		record, err := rd.Read()
		if err != nil {
			if err == ringbuf.ErrClosed {
				return nil
			}
			log.Printf("ringbuf read error: %v", err)
			continue
		}

		raw := record.RawSample
		if len(raw) < fcSize {
			log.Printf("ringbuf sample too small: got %d, want %d", len(raw), fcSize)
			continue
		}

		fc := rfscythe.NewFlowCore(raw)
		e.countPacket(int(fc.Bytes()))

		// ── Stage 4.5: zero-copy path ──────────────────────────────────
		// The kernel struct layout matches rfscythe.FlowCore exactly.
		// Forward raw bytes directly if binaryCh has capacity; fall through
		// to FlatBuffer path otherwise to avoid blocking on a hot path.
		select {
		case binaryCh <- append([]byte{0x00}, fc.Raw()...): // 0x00 = FlowCore frame tag
			e.countEvent()
			continue
		default:
		}

		// ── Stage 4: FlatBuffer table path (rich metadata) ────────────
		builder := builderPool.Get().(*flatbuffers.Builder)
		builder.Reset()

		rfscythe.FlowEventStart(builder)
		rfscythe.FlowEventAddFlowId(builder, fc.FlowId())
		rfscythe.FlowEventAddTs(builder, fc.Ts())
		rfscythe.FlowEventAddSrcIpv4(builder, fc.SrcIp())
		rfscythe.FlowEventAddDstIpv4(builder, fc.DstIp())
		rfscythe.FlowEventAddSrcPort(builder, fc.SrcPort())
		rfscythe.FlowEventAddDstPort(builder, fc.DstPort())
		rfscythe.FlowEventAddProto(builder, fc.Proto())
		rfscythe.FlowEventAddPackets(builder, fc.Packets())
		rfscythe.FlowEventAddBytes(builder, fc.Bytes())
		rfscythe.FlowEventAddEventType(builder, fc.EventType())
		rfscythe.FlowEventAddFlowHash(builder, fc.FlowHash())
		fbEvent := rfscythe.FlowEventEnd(builder)
		builder.Finish(fbEvent)

		buf := builder.FinishedBytes()
		out := make([]byte, 1+len(buf))
		out[0] = 0x01 // 0x01 = FlowEvent FlatBuffer frame tag
		copy(out[1:], buf)

		builderPool.Put(builder)
		binaryCh <- out
		e.countEvent()
	}
}

func flowToEvent(srcIP string, dstIP string, srcPort uint16, dstPort uint16, proto uint8, pkts uint64, bytesVal uint64, flowHash uint64, evType uint8, ts time.Time) *pb.Event {
	protoName := "unknown"
	switch proto {
	case rfscythe.TransportProtoTCP:
		protoName = "tcp"
	case rfscythe.TransportProtoUDP:
		protoName = "udp"
	case rfscythe.TransportProtoICMP:
		protoName = "icmp"
	}

	eventTypeName := map[uint8]string{
		rfscythe.FlowEventTypeFlowStart:  "flow_start",
		rfscythe.FlowEventTypeFlowUpdate: "flow_update",
		rfscythe.FlowEventTypeFlowEnd:    "flow_end",
	}
	typeName, ok := eventTypeName[evType]
	if !ok {
		typeName = "flow_update"
	}

	entities := []*pb.Entity{
		{Key: "src_ip", Value: srcIP},
		{Key: "dst_ip", Value: dstIP},
		{Key: "src_port", Value: strconv.Itoa(int(srcPort))},
		{Key: "dst_port", Value: strconv.Itoa(int(dstPort))},
		{Key: "proto", Value: protoName},
		{Key: "packets", Value: strconv.FormatUint(pkts, 10)},
		{Key: "bytes", Value: strconv.FormatUint(bytesVal, 10)},
		{Key: "flow_hash", Value: fmt.Sprintf("0x%016x", flowHash)},
	}

	return &pb.Event{
		EventId:   uuid.New().String(),
		Type:      typeName,
		Entities:  entities,
		Edges:     []string{fmt.Sprintf("%s -> %s", srcIP, dstIP)},
		Timestamp: ts.UTC().Format(time.RFC3339Nano),
	}
}

func intToIP(nn uint32) string {
	ip := make(net.IP, 4)
	binary.LittleEndian.PutUint32(ip, nn)
	return ip.String()
}

// ---------------------------------------------------------------------------
// HybridEngine — Stage 3
//
// Combines an always-on eBPF flow engine (semantic, low overhead) with an
// on-demand TPACKET_V3 raw engine that can be activated when the hypergraph
// detects an anomaly and needs full packet inspection.
//
// Usage via factory:
//   factory.Create(EngineConfig{Mode: "hybrid", Iface: "eth0", AllowFallback: true})
//
// Runtime control (e.g. from DriftGate / MCP):
//   engine.(*HybridEngine).ActivateRaw()
//   engine.(*HybridEngine).DeactivateRaw()
// ---------------------------------------------------------------------------

type HybridEngine struct {
	BaseEngine
	flowEngine CaptureEngine // eBPF — always running
	rawEngine  CaptureEngine // TPACKET_V3 — on demand

	rawActive bool
	rawDone   chan struct{}
	mu        sync.Mutex
}

func NewHybridEngine(cfg EngineConfig) CaptureEngine {
	return &HybridEngine{
		flowEngine: NewEBPFEngine(cfg),
		rawEngine:  NewTPacketV3Engine(cfg),
	}
}

func init() {
	RegisterEngine("hybrid", NewHybridEngine)
}

func (e *HybridEngine) Name() string { return "hybrid" }

func (e *HybridEngine) Capabilities() EngineCapabilities {
	return EngineCapabilities{
		ZeroCopy:          true,
		KernelOffload:     true,
		RequiresRoot:      true,
		RequiresXDP:       true,
		EmitsRawPackets:   true,
		EmitsFlows:        true,
		MaxThroughputMpps: 15.0,
	}
}

// Validate requires at least the eBPF engine to be usable.
func (e *HybridEngine) Validate() error {
	return e.flowEngine.Validate()
}

func (e *HybridEngine) Metrics() EngineMetrics {
	fm := e.flowEngine.Metrics()
	rm := e.rawEngine.Metrics()
	return EngineMetrics{
		PacketsProcessed: fm.PacketsProcessed + rm.PacketsProcessed,
		EventsEmitted:    fm.EventsEmitted + rm.EventsEmitted,
		BytesProcessed:   fm.BytesProcessed + rm.BytesProcessed,
		Dropped:          fm.Dropped + rm.Dropped,
	}
}

// Run starts the eBPF flow engine immediately. The raw engine is dormant
// until ActivateRaw() is called.
func (e *HybridEngine) Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error {
	return e.flowEngine.Run(eventCh, binaryCh, done)
}

// ActivateRaw starts the TPACKET_V3 raw capture engine alongside the flow
// engine. Safe to call multiple times (idempotent while already active).
func (e *HybridEngine) ActivateRaw(eventCh chan<- *pb.Event, binaryCh chan<- []byte) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if e.rawActive {
		return
	}

	if err := e.rawEngine.Validate(); err != nil {
		log.Printf("[HybridEngine] raw engine unavailable: %v", err)
		return
	}

	e.rawDone = make(chan struct{})
	e.rawActive = true

	go func() {
		log.Printf("[HybridEngine] raw capture engine ACTIVATED")
		if err := e.rawEngine.Run(eventCh, binaryCh, e.rawDone); err != nil {
			log.Printf("[HybridEngine] raw engine exited: %v", err)
		}
		e.mu.Lock()
		e.rawActive = false
		e.mu.Unlock()
		log.Printf("[HybridEngine] raw capture engine DEACTIVATED")
	}()
}

// DeactivateRaw stops the TPACKET_V3 raw engine. Safe to call when not active.
func (e *HybridEngine) DeactivateRaw() {
	e.mu.Lock()
	defer e.mu.Unlock()

	if !e.rawActive || e.rawDone == nil {
		return
	}
	close(e.rawDone)
}
