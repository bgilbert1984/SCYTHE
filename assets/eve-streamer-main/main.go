package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	flatbuffers "github.com/google/flatbuffers/go"
	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	Nerf "github.com/yourorg/eve-streamer/fb/Nerf"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"

	pb "github.com/yourorg/eve-streamer/pb"
)

const (
	defaultBatchSize = 100
	defaultBatchTime = 5 * time.Second
	defaultPort      = ":50051"
	eveJsonPath      = "/var/log/suricata/eve.json"

	// AF_PACKET capture configuration
	afPacketBlockSize  = 1024 * 1024
	afPacketFrameSize  = 2048
	afPacketBlockCount = 64
)

var (
	port          = flag.String("port", defaultPort, "gRPC server port")
	eveFile       = flag.String("eve", eveJsonPath, "Path to eve.json")
	batchSize     = flag.Int("batch-size", defaultBatchSize, "Number of events to batch")
	batchTime     = flag.Duration("batch-time", defaultBatchTime, "Time to wait before flushing batch")
	remoteAddr    = flag.String("remote", "", "Remote gRPC endpoint to stream events to (client mode)")
	mode          = flag.String("mode", "suricata", "Capture mode: 'suricata' (eve.json), 'afpacket' (standard), 'tpacket_v3' (zero-copy), or 'ebpf' (high-performance)")
	ifaceName     = flag.String("iface", "eth0", "Network interface for AF_PACKET/eBPF modes")
	httpPort      = flag.String("http-port", ":8081", "HTTP metrics port")
	allowFallback = flag.Bool("fallback", true, "Allow falling back to a compatible capture engine if the requested one is unavailable")
)

// Global reference to current engine for metrics reporting
var currentEngine CaptureEngine

// when running in shipper/client mode we keep the open stream here so
// batches can be sent continuously without dialing each time.
var remoteStream pb.EventStreamer_StreamEventsClient

type SuricataEvent struct {
	EventType string                 `json:"event_type"`
	Timestamp string                 `json:"timestamp"`
	SrcIP     string                 `json:"src_ip"`
	DestIP    string                 `json:"dest_ip"`
	SrcPort   int                    `json:"src_port"`
	DestPort  int                    `json:"dest_port"`
	Protocol  string                 `json:"proto"`
	Extra     map[string]interface{} `json:"-"`
}

type StreamServer struct {
	pb.UnimplementedEventStreamerServer
}

func (s *StreamServer) StreamEvents(stream pb.EventStreamer_StreamEventsServer) error {
	total := 0
	for {
		batch, err := stream.Recv()
		if err == io.EOF {
			return stream.SendAndClose(&pb.EventAck{
				Count:  int32(total),
				Status: "completed",
			})
		}
		if err != nil {
			return err
		}

		total += len(batch.Events)
		log.Printf("Received %d events (total %d)", len(batch.Events), total)
		// TODO: insert into hypergraph, run filtering/TAK-ML etc.
	}
}

func normalizeEvent(raw map[string]interface{}) *pb.Event {
	eventID := uuid.New().String()
	eventType := ""
	timestamp := ""

	if et, ok := raw["event_type"].(string); ok {
		eventType = et
	}
	if ts, ok := raw["timestamp"].(string); ok {
		timestamp = ts
	}

	entities := extractEntities(raw)
	edges := extractEdges(raw)

	return &pb.Event{
		EventId:   eventID,
		Type:      eventType,
		Entities:  entities,
		Edges:     edges,
		Timestamp: timestamp,
	}
}

func extractEntities(raw map[string]interface{}) []*pb.Entity {
	entities := []*pb.Entity{}

	fields := []string{"src_ip", "dest_ip", "src_port", "dest_port", "proto", "hostname"}
	for _, field := range fields {
		if val, ok := raw[field]; ok {
			entities = append(entities, &pb.Entity{
				Key:   field,
				Value: fmt.Sprintf("%v", val),
			})
		}
	}

	return entities
}

func extractEdges(raw map[string]interface{}) []string {
	edges := []string{}

	if srcIP, ok := raw["src_ip"].(string); ok {
		if destIP, ok := raw["dest_ip"].(string); ok {
			edges = append(edges, fmt.Sprintf("%s -> %s", srcIP, destIP))
		}
	}

	return edges
}

func sendBatch(batch *pb.EventBatch) {
	log.Printf("Sending batch with %d events", len(batch.Events))
	if remoteStream != nil {
		if err := remoteStream.Send(batch); err != nil {
			log.Printf("failed to send batch to remote: %v", err)
		}
	} else {
		// local mode – nothing to do
	}
}

// ============================================================================
// AF_PACKET ZERO-COPY CAPTURE (Stage 1 — Simplified, Correct First)
// ============================================================================

// htons converts host-endian uint16 to network-endian (big-endian).
func htons(i uint16) uint16 {
	return (i<<8)&0xff00 | i>>8
}

// Extract 5-tuple from raw packet bytes (Ethernet + IPv4 + TCP/UDP).
func extractPacketFromBuffer(pktData []byte) (srcIP uint32, dstIP uint32, srcPort uint16, dstPort uint16, proto uint8, ok bool) {
	// Minimum: Ethernet (14) + IPv4 (20) + UDP (8) = 42 bytes
	if len(pktData) < 42 {
		return 0, 0, 0, 0, 0, false
	}

	// Parse Ethernet: check EtherType at offset 12–14
	etherType := binary.BigEndian.Uint16(pktData[12:14])
	if etherType != 0x0800 { // IPv4 only
		return 0, 0, 0, 0, 0, false
	}

	// IPv4 header starts at offset 14
	ipHeader := pktData[14:]
	if len(ipHeader) < 20 {
		return 0, 0, 0, 0, 0, false
	}

	// IHL: low 4 bits of first byte => header length in 32-bit words
	ihl := (ipHeader[0] & 0x0f) * 4
	if int(ihl) > len(ipHeader) {
		return 0, 0, 0, 0, 0, false
	}

	// Protocol field at offset 9
	proto = ipHeader[9]

	// Source and destination IPs (offsets 12–16 and 16–20)
	// We return them as LittleEndian uint32 to match eBPF memory layout on x86
	srcIP = binary.LittleEndian.Uint32(ipHeader[12:16])
	dstIP = binary.LittleEndian.Uint32(ipHeader[16:20])

	// Transport layer starts at ipHeader + ihl
	// Only TCP (6) and UDP (17) for now
	if proto != 6 && proto != 17 {
		return 0, 0, 0, 0, proto, false
	}

	if int(ihl)+4 > len(ipHeader) {
		return 0, 0, 0, 0, 0, false
	}

	transportHeader := ipHeader[ihl:]
	srcPort = binary.BigEndian.Uint16(transportHeader[0:2])
	dstPort = binary.BigEndian.Uint16(transportHeader[2:4])

	return srcIP, dstIP, srcPort, dstPort, proto, true
}

// packetToBinaryFlow serializes a packet 5-tuple to a FlatBuffer binary message.
func packetToBinaryFlow(builder *flatbuffers.Builder, srcIP uint32, dstIP uint32, srcPort uint16, dstPort uint16, proto uint8, length uint64, ts time.Time) []byte {
	builder.Reset()

	Nerf.FlowEventStart(builder)
	Nerf.FlowEventAddFlowId(builder, 0)
	Nerf.FlowEventAddSrcIp(builder, srcIP)
	Nerf.FlowEventAddDstIp(builder, dstIP)
	Nerf.FlowEventAddSrcPort(builder, srcPort)
	Nerf.FlowEventAddDstPort(builder, dstPort)
	Nerf.FlowEventAddProto(builder, proto)
	Nerf.FlowEventAddPackets(builder, 1)
	Nerf.FlowEventAddBytes(builder, length)
	Nerf.FlowEventAddFlags(builder, 0)
	Nerf.FlowEventAddEventType(builder, 0) // start/packet
	Nerf.FlowEventAddTimestamp(builder, uint64(ts.UnixNano()))

	fbEvent := Nerf.FlowEventEnd(builder)
	builder.Finish(fbEvent)

	buf := builder.FinishedBytes()
	out := make([]byte, len(buf))
	copy(out, buf)
	return out
}

// Convert packet 5-tuple to pb.Event (Legacy/JSON path).
func packetToEvent(srcIP uint32, dstIP uint32, srcPort uint16, dstPort uint16, proto uint8, ts time.Time) *pb.Event {
	srcStr := net.IP{byte(srcIP >> 24), byte(srcIP >> 16), byte(srcIP >> 8), byte(srcIP)}.String()
	dstStr := net.IP{byte(dstIP >> 24), byte(dstIP >> 16), byte(dstIP >> 8), byte(dstIP)}.String()

	protoName := "unknown"
	switch proto {
	case 6:
		protoName = "tcp"
	case 17:
		protoName = "udp"
	}

	entities := []*pb.Entity{
		{Key: "src_ip", Value: srcStr},
		{Key: "dst_ip", Value: dstStr},
		{Key: "src_port", Value: strconv.Itoa(int(srcPort))},
		{Key: "dst_port", Value: strconv.Itoa(int(dstPort))},
		{Key: "proto", Value: protoName},
	}

	edges := []string{
		fmt.Sprintf("%s -> %s", srcStr, dstStr),
	}

	return &pb.Event{
		EventId:   uuid.New().String(),
		Type:      "packet",
		Entities:  entities,
		Edges:     edges,
		Timestamp: ts.UTC().Format(time.RFC3339Nano),
	}
}

func authStreamInterceptor(ctx context.Context, desc *grpc.StreamDesc, cc *grpc.ClientConn, method string, streamer grpc.Streamer, opts ...grpc.CallOption) (grpc.ClientStream, error) {
	if token := os.Getenv("STREAM_TOKEN"); token != "" {
		md := metadata.Pairs("authorization", "Bearer "+token)
		ctx = metadata.NewOutgoingContext(ctx, md)
	}
	return streamer(ctx, desc, cc, method, opts...)
}

func initRemote(addr string) error {
	ctx := context.Background()
	opts := []grpc.DialOption{
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStreamInterceptor(authStreamInterceptor),
	}
	conn, err := grpc.DialContext(ctx, addr, opts...)
	if err != nil {
		return err
	}
	client := pb.NewEventStreamerClient(conn)
	remoteStream, err = client.StreamEvents(ctx)
	return err
}

func main() {
	flag.Parse()

	if *remoteAddr != "" {
		if err := initRemote(*remoteAddr); err != nil {
			log.Fatalf("failed to initialise remote stream: %v", err)
		}
		log.Printf("acting as client, forwarding events to %s", *remoteAddr)
	}

	lis, err := net.Listen("tcp", *port)
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}
	defer lis.Close()

	grpcServer := grpc.NewServer()
	pb.RegisterEventStreamerServer(grpcServer, &StreamServer{})

	// Initialize Factory
	factory := NewCaptureEngineFactory()

	// Create capture engine based on mode via Factory
	engine, err := factory.Create(EngineConfig{
		Mode:          *mode,
		Iface:         *ifaceName,
		EveFile:       *eveFile,
		BlockSize:     afPacketBlockSize,
		BlockCount:    afPacketBlockCount,
		FrameSize:     afPacketFrameSize,
		AllowFallback: *allowFallback,
	})
	if err != nil {
		log.Fatalf("failed to create capture engine: %v", err)
	}

	currentEngine = engine
	log.Printf("Using engine: %s (capabilities: %+v)", engine.Name(), engine.Capabilities())

	// HTTP Metrics Endpoint
	http.HandleFunc("/capture/metrics", func(w http.ResponseWriter, r *http.Request) {
		metrics := currentEngine.Metrics()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(metrics)
	})
	http.HandleFunc("/ws", handleWS)

	go func() {
		log.Printf("Starting HTTP metrics server on %s", *httpPort)
		if err := http.ListenAndServe(*httpPort, nil); err != nil {
			log.Printf("HTTP server error: %v", err)
		}
	}()

	// event channel and batching goroutine
	eventCh := make(chan *pb.Event, 4096)
	binaryCh := make(chan []byte, 4096)
	done := make(chan struct{})

	go func() {
		batch := &pb.EventBatch{Events: []*pb.Event{}}
		ticker := time.NewTicker(*batchTime)
		defer ticker.Stop()
		for {
			select {
			case ev := <-eventCh:
				batch.Events = append(batch.Events, ev)
				if len(batch.Events) >= *batchSize {
					sendBatch(batch)
					batch = &pb.EventBatch{Events: []*pb.Event{}}
				}
			case <-ticker.C:
				if len(batch.Events) > 0 {
					sendBatch(batch)
					batch = &pb.EventBatch{Events: []*pb.Event{}}
				}
			case msg := <-binaryCh:
				broadcastBinary(msg)
			case <-done:
				// flush remaining
				if len(batch.Events) > 0 {
					sendBatch(batch)
				}
				return
			}
		}
	}()

	// start capture engine
	go func() {
		if err := engine.Run(eventCh, binaryCh, done); err != nil {
			log.Printf("Engine runtime error: %v", err)
			// In production, we might want to trigger a fallback or exit here
		}
	}()

	log.Printf("Starting gRPC server on %s", *port)

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	var wg sync.WaitGroup
	wg.Add(1)

	go func() {
		defer wg.Done()
		if err := grpcServer.Serve(lis); err != nil {
			log.Printf("Server error: %v", err)
		}
	}()

	<-sigChan
	log.Println("Shutting down...")

	// signal capture engine / batcher to stop
	close(done)

	if remoteStream != nil {
		if ack, err := remoteStream.CloseAndRecv(); err != nil {
			log.Printf("remote close error: %v", err)
		} else {
			log.Printf("final ack from remote: count=%d status=%s", ack.Count, ack.Status)
		}
	}

	grpcServer.GracefulStop()
	wg.Wait()
}

var (
	wsUpgrader = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
	wsConns   = make(map[*websocket.Conn]bool)
	wsConnsMu sync.Mutex
)

func handleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := wsUpgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("WS upgrade failed: %v", err)
		return
	}
	wsConnsMu.Lock()
	wsConns[conn] = true
	wsConnsMu.Unlock()
	log.Printf("WS client connected: %s", conn.RemoteAddr())

	// keep connection alive until client closes
	defer func() {
		wsConnsMu.Lock()
		delete(wsConns, conn)
		wsConnsMu.Unlock()
		conn.Close()
		log.Printf("WS client disconnected: %s", conn.RemoteAddr())
	}()

	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			break
		}
	}
}

func broadcastBinary(msg []byte) {
	wsConnsMu.Lock()
	defer wsConnsMu.Unlock()
	for conn := range wsConns {
		if err := conn.WriteMessage(websocket.BinaryMessage, msg); err != nil {
			log.Printf("WS write error to %s: %v", conn.RemoteAddr(), err)
			conn.Close()
			delete(wsConns, conn)
		}
	}
}
