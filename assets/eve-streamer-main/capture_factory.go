package main

import (
	"errors"
	"fmt"
	"os"
	"sync"

	pb "github.com/yourorg/eve-streamer/pb"
)

// ─────────────────────────────────────────────────────────
//  Engine Capabilities & Metrics
// ─────────────────────────────────────────────────────────
//

type EngineCapabilities struct {
	ZeroCopy          bool
	KernelOffload     bool
	RequiresRoot      bool
	RequiresXDP       bool
	RequiresHugepages bool
	EmitsRawPackets   bool
	EmitsFlows        bool
	MaxThroughputMpps float64
}

type EngineMetrics struct {
	PacketsProcessed uint64  `json:"packets_processed"`
	EventsEmitted    uint64  `json:"events_emitted"`
	BytesProcessed   uint64  `json:"bytes_processed"`
	Dropped          uint64  `json:"dropped"`
	RingUtilization  float64 `json:"ring_utilization"`
	CpuPercent       float64 `json:"cpu_percent"`
}

//
// ─────────────────────────────────────────────────────────
//  CaptureEngine Interface
// ─────────────────────────────────────────────────────────
//

type CaptureEngine interface {
	Name() string
	Capabilities() EngineCapabilities
	Metrics() EngineMetrics
	Validate() error
	Run(eventCh chan<- *pb.Event, binaryCh chan<- []byte, done <-chan struct{}) error
}

//
// ─────────────────────────────────────────────────────────
//  EngineConfig
// ─────────────────────────────────────────────────────────
//

type EngineConfig struct {
	Mode          string
	Iface         string
	EveFile       string // Added for Suricata mode
	BlockSize     int    // For TPACKET_V3
	BlockCount    int    // For TPACKET_V3
	FrameSize     int    // For TPACKET_V3
	AllowFallback bool
}

//
// ─────────────────────────────────────────────────────────
//  Registry
// ─────────────────────────────────────────────────────────
//

type EngineConstructor func(EngineConfig) CaptureEngine

var (
	engineRegistry = make(map[string]EngineConstructor)
	registryMu     sync.RWMutex
)

func RegisterEngine(name string, constructor EngineConstructor) {
	registryMu.Lock()
	defer registryMu.Unlock()

	if _, exists := engineRegistry[name]; exists {
		panic(fmt.Sprintf("engine already registered: %s", name))
	}

	engineRegistry[name] = constructor
}

func listRegisteredModes() []string {
	registryMu.RLock()
	defer registryMu.RUnlock()

	modes := make([]string, 0, len(engineRegistry))
	for k := range engineRegistry {
		modes = append(modes, k)
	}
	return modes
}

//
// ─────────────────────────────────────────────────────────
//  CaptureEngineFactory
// ─────────────────────────────────────────────────────────
//

type CaptureEngineFactory struct{}

func NewCaptureEngineFactory() *CaptureEngineFactory {
	return &CaptureEngineFactory{}
}

type EngineRequirements struct {
	MustEmitFlows     bool
	MustBeZeroCopy    bool
	MustKernelOffload bool
}

func (f *CaptureEngineFactory) CreateWithRequirements(req EngineRequirements, cfg EngineConfig) (CaptureEngine, error) {
	registryMu.RLock()
	defer registryMu.RUnlock()

	// Find best engine matching requirements
	// We iterate through all registered engines and check their capabilities
	var bestEngine CaptureEngine
	var bestScore int

	for _, constructor := range engineRegistry {
		engine := constructor(cfg)
		caps := engine.Capabilities()

		if req.MustEmitFlows && !caps.EmitsFlows {
			continue
		}
		if req.MustBeZeroCopy && !caps.ZeroCopy {
			continue
		}
		if req.MustKernelOffload && !caps.KernelOffload {
			continue
		}

		// Scoring heuristic: prefer eBPF > TPACKET_V3 > AF_PACKET > Suricata
		score := 0
		if caps.KernelOffload {
			score += 100
		}
		if caps.ZeroCopy {
			score += 50
		}
		if caps.EmitsFlows {
			score += 25
		}

		if score > bestScore || bestEngine == nil {
			bestScore = score
			bestEngine = engine
		}
	}

	if bestEngine == nil {
		return nil, errors.New("no capture engine matches requirements")
	}

	if err := bestEngine.Validate(); err != nil {
		return nil, fmt.Errorf("best matching engine (%s) failed validation: %w", bestEngine.Name(), err)
	}

	return bestEngine, nil
}

func (f *CaptureEngineFactory) Create(cfg EngineConfig) (CaptureEngine, error) {
	registryMu.RLock()
	constructor, exists := engineRegistry[cfg.Mode]
	registryMu.RUnlock()

	if !exists {
		return nil, fmt.Errorf("unknown capture mode: %s (available: %v)",
			cfg.Mode, listRegisteredModes())
	}

	engine := constructor(cfg)

	if err := engine.Validate(); err != nil {
		if cfg.AllowFallback {
			return f.fallback(cfg, err)
		}
		return nil, fmt.Errorf("engine validation failed for %s: %w", cfg.Mode, err)
	}

	return engine, nil
}

//
// ─────────────────────────────────────────────────────────
//  Intelligent Fallback
// ─────────────────────────────────────────────────────────
//

func (f *CaptureEngineFactory) fallback(cfg EngineConfig, cause error) (CaptureEngine, error) {
	// Ordered fallback priority
	fallbackOrder := []string{
		"ebpf",
		"tpacket_v3",
		"afpacket",
		"suricata",
	}

	for _, mode := range fallbackOrder {
		if mode == cfg.Mode {
			continue
		}

		registryMu.RLock()
		constructor, exists := engineRegistry[mode]
		registryMu.RUnlock()

		if !exists {
			continue
		}

		engine := constructor(EngineConfig{
			Mode:          mode,
			Iface:         cfg.Iface,
			EveFile:       cfg.EveFile,
			BlockSize:     cfg.BlockSize,
			BlockCount:    cfg.BlockCount,
			FrameSize:     cfg.FrameSize,
			AllowFallback: false,
		})

		if err := engine.Validate(); err == nil {
			fmt.Fprintf(os.Stderr,
				"[CaptureFactory] Fallback: %s → %s (reason: %v)\n",
				cfg.Mode, mode, cause,
			)
			return engine, nil
		}
	}

	return nil, fmt.Errorf("no valid fallback engine available (original error: %w)", cause)
}

//
// ─────────────────────────────────────────────────────────
//  Introspection (for MCP / API export)
// ─────────────────────────────────────────────────────────
//

type EngineDescriptor struct {
	Name         string             `json:"name"`
	Capabilities EngineCapabilities `json:"capabilities"`
}

func (f *CaptureEngineFactory) ListEngines() []EngineDescriptor {
	registryMu.RLock()
	defer registryMu.RUnlock()

	descriptors := []EngineDescriptor{}

	for name, constructor := range engineRegistry {
		engine := constructor(EngineConfig{
			Mode:  name,
			Iface: "lo", // dummy interface for metadata
		})

		descriptors = append(descriptors, EngineDescriptor{
			Name:         engine.Name(),
			Capabilities: engine.Capabilities(),
		})
	}

	return descriptors
}

//
// ─────────────────────────────────────────────────────────
//  Utility Validation Helpers
// ─────────────────────────────────────────────────────────
//

func requireRoot() error {
	if os.Geteuid() != 0 {
		return errors.New("requires root privileges (CAP_NET_RAW or equivalent)")
	}
	return nil
}
