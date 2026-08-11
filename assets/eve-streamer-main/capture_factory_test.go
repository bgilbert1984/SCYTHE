package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	pb "github.com/yourorg/eve-streamer/pb"
)

func TestSuricataEngineContinuesAfterInitialEOF(t *testing.T) {
	path := filepath.Join(t.TempDir(), "eve.json")
	if err := os.WriteFile(path, nil, 0600); err != nil {
		t.Fatal(err)
	}
	engine := NewSuricataEngine(EngineConfig{EveFile: path})
	events := make(chan *pb.Event, 1)
	binary := make(chan []byte, 1)
	done := make(chan struct{})
	errCh := make(chan error, 1)
	go func() { errCh <- engine.Run(events, binary, done) }()
	t.Cleanup(func() { close(done); <-errCh })
	time.Sleep(150 * time.Millisecond)

	record, _ := json.Marshal(map[string]interface{}{
		"timestamp": "2026-08-07T03:30:00Z", "event_type": "test_flow",
		"src_ip": "10.0.0.1", "dest_ip": "8.8.8.8",
		"src_port": 49152, "dest_port": 443, "proto": "TCP",
	})
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.Write(append(record, '\n')); err != nil {
		t.Fatal(err)
	}
	file.Close()

	select {
	case event := <-events:
		if event.Type != "test_flow" {
			t.Fatalf("unexpected event type %q", event.Type)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("tailer did not resume after EOF")
	}
}

func TestSuricataEngineFollowsNewestRotatedFile(t *testing.T) {
	dir := t.TempDir()
	first := filepath.Join(dir, "eve-2026-08-07.json")
	if err := os.WriteFile(first, nil, 0600); err != nil {
		t.Fatal(err)
	}
	engine := NewSuricataEngine(EngineConfig{EveFile: filepath.Join(dir, "eve-*.json")})
	events := make(chan *pb.Event, 1)
	binary := make(chan []byte, 1)
	done := make(chan struct{})
	errCh := make(chan error, 1)
	go func() { errCh <- engine.Run(events, binary, done) }()
	t.Cleanup(func() { close(done); <-errCh })
	time.Sleep(150 * time.Millisecond)

	record, _ := json.Marshal(map[string]interface{}{
		"timestamp": "2026-08-08T02:50:00Z", "event_type": "test_rotated_flow",
		"src_ip": "10.0.0.2", "dest_ip": "1.1.1.1",
		"src_port": 49153, "dest_port": 443, "proto": "TCP",
	})
	second := filepath.Join(dir, "eve-2026-08-08.json")
	if err := os.WriteFile(second, append(record, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	now := time.Now().Add(time.Second)
	if err := os.Chtimes(second, now, now); err != nil {
		t.Fatal(err)
	}

	select {
	case event := <-events:
		if event.Type != "test_rotated_flow" {
			t.Fatalf("unexpected event type %q", event.Type)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("tailer did not follow the newest rotated Eve file")
	}
}

func TestSuricataEngineReplaysBoundedRecentRecordsThenTails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "eve.json")
	var contents []byte
	for i := 1; i <= 3; i++ {
		record, _ := json.Marshal(map[string]interface{}{
			"timestamp": "2026-08-08T02:50:00Z", "event_type": "flow",
			"src_ip": "10.0.0.1", "dest_ip": "8.8.8.8",
			"src_port": 49000 + i, "dest_port": 443, "proto": "TCP",
		})
		contents = append(contents, append(record, '\n')...)
	}
	if err := os.WriteFile(path, contents, 0600); err != nil {
		t.Fatal(err)
	}
	engine := NewSuricataEngine(EngineConfig{EveFile: path, ReplayLast: 2})
	events := make(chan *pb.Event, 4)
	binary := make(chan []byte, 1)
	done := make(chan struct{})
	errCh := make(chan error, 1)
	go func() { errCh <- engine.Run(events, binary, done) }()
	t.Cleanup(func() { close(done); <-errCh })

	for expectedPort := 49002; expectedPort <= 49003; expectedPort++ {
		select {
		case event := <-events:
			if event.Entities[2].Value != fmt.Sprint(expectedPort) {
				t.Fatalf("unexpected replay port %q", event.Entities[2].Value)
			}
			last := event.Entities[len(event.Entities)-1]
			if last.Key != "scythe_ingest_mode" || last.Value != "bootstrap_replay" {
				t.Fatalf("missing replay marker: %+v", last)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("bounded replay did not arrive")
		}
	}
	select {
	case extra := <-events:
		t.Fatalf("replay exceeded bound: %+v", extra)
	case <-time.After(150 * time.Millisecond):
	}
}

func TestNormalizeEventIDIsStableAcrossReplay(t *testing.T) {
	raw := map[string]interface{}{"timestamp": "2026-08-08T02:50:00Z", "event_type": "flow",
		"src_ip": "10.0.0.1", "dest_ip": "8.8.8.8"}
	first, second := normalizeEvent(raw), normalizeEvent(raw)
	if first.EventId != second.EventId || !strings.HasPrefix(first.EventId, "eve-") {
		t.Fatalf("event IDs are not stable: %q %q", first.EventId, second.EventId)
	}
}

func TestCaptureEngineFactory(t *testing.T) {
	factory := NewCaptureEngineFactory()

	// Suricata mode is the unprivileged deterministic factory path.
	evePath := filepath.Join(t.TempDir(), "eve.json")
	if err := os.WriteFile(evePath, nil, 0600); err != nil {
		t.Fatal(err)
	}
	cfg := EngineConfig{
		Mode:          "suricata",
		EveFile:       evePath,
		Iface:         "lo",
		AllowFallback: true,
	}

	engine, err := factory.Create(cfg)
	if err != nil {
		t.Fatalf("Factory failed to create engine: %v", err)
	}

	if engine == nil {
		t.Fatal("Factory returned nil engine")
	}

	t.Logf("Created engine: %s", engine.Name())

	// Test case: unknown mode
	cfgUnknown := EngineConfig{
		Mode: "non-existent-engine",
	}
	_, err = factory.Create(cfgUnknown)
	if err == nil {
		t.Error("Factory should have failed for unknown engine mode")
	}
}

func TestListEngines(t *testing.T) {
	factory := NewCaptureEngineFactory()
	engines := factory.ListEngines()

	if len(engines) == 0 {
		t.Error("Factory returned empty engine list")
	}

	for _, eng := range engines {
		t.Logf("Found registered engine: %s (RawPackets: %v)", eng.Name, eng.Capabilities.EmitsRawPackets)
	}
}
