// Code generated from fb/flow.fbs (rfscythe namespace).
// Run `make fb` to regenerate via flatc once the toolchain is installed.

package rfscythe

import flatbuffers "github.com/google/flatbuffers/go"

// ── Enumerations ──────────────────────────────────────────────────────────────

type FlowEventType = byte

const (
	FlowEventTypeFlowStart  FlowEventType = 0
	FlowEventTypeFlowUpdate FlowEventType = 1
	FlowEventTypeFlowEnd    FlowEventType = 2
)

type TransportProto = byte

const (
	TransportProtoICMP  TransportProto = 1
	TransportProtoTCP   TransportProto = 6
	TransportProtoUDP   TransportProto = 17
	TransportProtoOTHER TransportProto = 255
)

// ── IPv6Addr struct (fixed-size, embedded in FlowEvent table) ─────────────────

type IPv6Addr struct {
	Hi uint64
	Lo uint64
}

// ── FlowEvent table ────────────────────────────────────────────────────────────

// VTable offsets for FlowEvent fields (4 + 2*fieldIndex).
const (
	FlowEventVT_FLOW_ID       = 4
	FlowEventVT_TS            = 6
	FlowEventVT_SRC_IPV4      = 8
	FlowEventVT_DST_IPV4      = 10
	FlowEventVT_SRC_IPV6      = 12
	FlowEventVT_DST_IPV6      = 14
	FlowEventVT_SRC_PORT      = 16
	FlowEventVT_DST_PORT      = 18
	FlowEventVT_PROTO         = 20
	FlowEventVT_PACKETS       = 22
	FlowEventVT_BYTES         = 24
	FlowEventVT_TCP_FLAGS     = 26
	FlowEventVT_IFINDEX       = 28
	FlowEventVT_CPU           = 30
	FlowEventVT_EVENT_TYPE    = 32
	FlowEventVT_ENTROPY_HINT  = 34
	FlowEventVT_ANOMALY_SCORE = 36
	FlowEventVT_FLOW_HASH     = 38
)

const FlowEventNumFields = 18

// FlowEvent wraps a FlatBuffers table for reading decoded flow events.
type FlowEvent struct {
	_tab flatbuffers.Table
}

func GetRootAsFlowEvent(buf []byte, offset flatbuffers.UOffsetT) *FlowEvent {
	n := flatbuffers.GetUOffsetT(buf[offset:])
	x := &FlowEvent{}
	x.Init(buf, n+offset)
	return x
}

func (rcv *FlowEvent) Init(buf []byte, i flatbuffers.UOffsetT) {
	rcv._tab.Bytes = buf
	rcv._tab.Pos = i
}

func (rcv *FlowEvent) Table() flatbuffers.Table { return rcv._tab }

func (rcv *FlowEvent) FlowId() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_FLOW_ID))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Ts() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_TS))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) SrcIpv4() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_SRC_IPV4))
	if o != 0 {
		return rcv._tab.GetUint32(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) DstIpv4() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_DST_IPV4))
	if o != 0 {
		return rcv._tab.GetUint32(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) SrcPort() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_SRC_PORT))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) DstPort() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_DST_PORT))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Proto() TransportProto {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_PROTO))
	if o != 0 {
		return rcv._tab.GetByte(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Packets() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_PACKETS))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Bytes() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_BYTES))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) TcpFlags() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_TCP_FLAGS))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Ifindex() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_IFINDEX))
	if o != 0 {
		return rcv._tab.GetUint32(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Cpu() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_CPU))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) EventType() FlowEventType {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_EVENT_TYPE))
	if o != 0 {
		return rcv._tab.GetByte(o + rcv._tab.Pos)
	}
	return FlowEventTypeFlowStart
}

func (rcv *FlowEvent) EntropyHint() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_ENTROPY_HINT))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) AnomalyScore() uint16 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_ANOMALY_SCORE))
	if o != 0 {
		return rcv._tab.GetUint16(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) FlowHash() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_FLOW_HASH))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

// ── Builder functions ─────────────────────────────────────────────────────────

func FlowEventStart(builder *flatbuffers.Builder) {
	builder.StartObject(FlowEventNumFields)
}

func FlowEventAddFlowId(builder *flatbuffers.Builder, flowId uint64) {
	builder.PrependUint64Slot(0, flowId, 0)
}

func FlowEventAddTs(builder *flatbuffers.Builder, ts uint64) {
	builder.PrependUint64Slot(1, ts, 0)
}

func FlowEventAddSrcIpv4(builder *flatbuffers.Builder, srcIpv4 uint32) {
	builder.PrependUint32Slot(2, srcIpv4, 0)
}

func FlowEventAddDstIpv4(builder *flatbuffers.Builder, dstIpv4 uint32) {
	builder.PrependUint32Slot(3, dstIpv4, 0)
}

func FlowEventAddSrcPort(builder *flatbuffers.Builder, srcPort uint16) {
	builder.PrependUint16Slot(6, srcPort, 0)
}

func FlowEventAddDstPort(builder *flatbuffers.Builder, dstPort uint16) {
	builder.PrependUint16Slot(7, dstPort, 0)
}

func FlowEventAddProto(builder *flatbuffers.Builder, proto TransportProto) {
	builder.PrependByteSlot(8, proto, 0)
}

func FlowEventAddPackets(builder *flatbuffers.Builder, packets uint64) {
	builder.PrependUint64Slot(9, packets, 0)
}

func FlowEventAddBytes(builder *flatbuffers.Builder, bytesVal uint64) {
	builder.PrependUint64Slot(10, bytesVal, 0)
}

func FlowEventAddTcpFlags(builder *flatbuffers.Builder, tcpFlags uint16) {
	builder.PrependUint16Slot(11, tcpFlags, 0)
}

func FlowEventAddIfindex(builder *flatbuffers.Builder, ifindex uint32) {
	builder.PrependUint32Slot(12, ifindex, 0)
}

func FlowEventAddCpu(builder *flatbuffers.Builder, cpu uint16) {
	builder.PrependUint16Slot(13, cpu, 0)
}

func FlowEventAddEventType(builder *flatbuffers.Builder, eventType FlowEventType) {
	builder.PrependByteSlot(14, eventType, 0)
}

func FlowEventAddEntropyHint(builder *flatbuffers.Builder, entropyHint uint16) {
	builder.PrependUint16Slot(15, entropyHint, 0)
}

func FlowEventAddAnomalyScore(builder *flatbuffers.Builder, anomalyScore uint16) {
	builder.PrependUint16Slot(16, anomalyScore, 0)
}

func FlowEventAddFlowHash(builder *flatbuffers.Builder, flowHash uint64) {
	builder.PrependUint64Slot(17, flowHash, 0)
}

func FlowEventEnd(builder *flatbuffers.Builder) flatbuffers.UOffsetT {
	return builder.EndObject()
}
