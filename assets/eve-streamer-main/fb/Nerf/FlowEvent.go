// Code generated from flow.fbs schema (manually authored — run `make fb` to regenerate via flatc).

package Nerf

import flatbuffers "github.com/google/flatbuffers/go"

// FlowEventNumFields matches the number of fields in the FlowEvent table.
const FlowEventNumFields = 11

// VTable offsets for FlowEvent fields (4 + 2*fieldIndex).
const (
	FlowEventVT_FLOW_ID    = 4
	FlowEventVT_SRC_IP     = 6
	FlowEventVT_DST_IP     = 8
	FlowEventVT_SRC_PORT   = 10
	FlowEventVT_DST_PORT   = 12
	FlowEventVT_PROTO      = 14
	FlowEventVT_PACKETS    = 16
	FlowEventVT_BYTES      = 18
	FlowEventVT_FLAGS      = 20
	FlowEventVT_EVENT_TYPE = 22
	FlowEventVT_TIMESTAMP  = 24
)

// FlowEvent is the Go wrapper for the Nerf.FlowEvent FlatBuffers table.
type FlowEvent struct {
	_tab flatbuffers.Table
}

// GetRootAsFlowEvent deserialises a FlowEvent from a byte buffer.
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

func (rcv *FlowEvent) Table() flatbuffers.Table {
	return rcv._tab
}

func (rcv *FlowEvent) FlowId() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_FLOW_ID))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) SrcIp() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_SRC_IP))
	if o != 0 {
		return rcv._tab.GetUint32(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) DstIp() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_DST_IP))
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

func (rcv *FlowEvent) Proto() byte {
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

func (rcv *FlowEvent) Flags() uint32 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_FLAGS))
	if o != 0 {
		return rcv._tab.GetUint32(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) EventType() byte {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_EVENT_TYPE))
	if o != 0 {
		return rcv._tab.GetByte(o + rcv._tab.Pos)
	}
	return 0
}

func (rcv *FlowEvent) Timestamp() uint64 {
	o := flatbuffers.UOffsetT(rcv._tab.Offset(FlowEventVT_TIMESTAMP))
	if o != 0 {
		return rcv._tab.GetUint64(o + rcv._tab.Pos)
	}
	return 0
}

// ── Builder functions ────────────────────────────────────────────────────────

func FlowEventStart(builder *flatbuffers.Builder) {
	builder.StartObject(FlowEventNumFields)
}

func FlowEventAddFlowId(builder *flatbuffers.Builder, flowId uint64) {
	builder.PrependUint64Slot(0, flowId, 0)
}

func FlowEventAddSrcIp(builder *flatbuffers.Builder, srcIp uint32) {
	builder.PrependUint32Slot(1, srcIp, 0)
}

func FlowEventAddDstIp(builder *flatbuffers.Builder, dstIp uint32) {
	builder.PrependUint32Slot(2, dstIp, 0)
}

func FlowEventAddSrcPort(builder *flatbuffers.Builder, srcPort uint16) {
	builder.PrependUint16Slot(3, srcPort, 0)
}

func FlowEventAddDstPort(builder *flatbuffers.Builder, dstPort uint16) {
	builder.PrependUint16Slot(4, dstPort, 0)
}

func FlowEventAddProto(builder *flatbuffers.Builder, proto byte) {
	builder.PrependByteSlot(5, proto, 0)
}

func FlowEventAddPackets(builder *flatbuffers.Builder, packets uint64) {
	builder.PrependUint64Slot(6, packets, 0)
}

func FlowEventAddBytes(builder *flatbuffers.Builder, bytesVal uint64) {
	builder.PrependUint64Slot(7, bytesVal, 0)
}

func FlowEventAddFlags(builder *flatbuffers.Builder, flags uint32) {
	builder.PrependUint32Slot(8, flags, 0)
}

func FlowEventAddEventType(builder *flatbuffers.Builder, eventType byte) {
	builder.PrependByteSlot(9, eventType, 0)
}

func FlowEventAddTimestamp(builder *flatbuffers.Builder, timestamp uint64) {
	builder.PrependUint64Slot(10, timestamp, 0)
}

func FlowEventEnd(builder *flatbuffers.Builder) flatbuffers.UOffsetT {
	return builder.EndObject()
}
