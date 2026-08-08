// Code generated from fb/flow.fbs (rfscythe namespace) — FlowCore struct.
//
// FlowCore is a fixed-size FlatBuffers struct whose binary layout exactly
// mirrors the kernel struct flow_core in bpf_capture.c.  When the kernel
// struct is filled correctly, Go can forward record.RawSample bytes directly
// over WebSocket — no re-serialisation step.
//
// Binary layout (56 bytes, little-endian):
//   [0]  flow_id    : ulong  (8)
//   [8]  ts         : ulong  (8)
//   [16] src_ip     : uint   (4)
//   [20] dst_ip     : uint   (4)
//   [24] src_port   : ushort (2)
//   [26] dst_port   : ushort (2)
//   [28] proto      : ubyte  (1)
//   [29] event_type : ubyte  (1)
//   [30] <implicit 2-byte alignment pad>
//   [32] packets    : ulong  (8)
//   [40] bytes      : ulong  (8)
//   [48] flow_hash  : ulong  (8)
//   Total: 56 bytes

package rfscythe

import (
	"encoding/binary"
)

const FlowCoreSize = 56

// FlowCore provides zero-copy access to a kernel-emitted flow_core byte slice.
// It does not own the underlying buffer; the caller must ensure the buffer
// remains valid for the lifetime of the FlowCore reference.
type FlowCore struct {
	buf []byte
}

// NewFlowCore wraps an existing byte slice.  Returns nil if the buffer is
// smaller than FlowCoreSize.
func NewFlowCore(buf []byte) *FlowCore {
	if len(buf) < FlowCoreSize {
		return nil
	}
	return &FlowCore{buf: buf}
}

func (f *FlowCore) FlowId() uint64    { return binary.LittleEndian.Uint64(f.buf[0:]) }
func (f *FlowCore) Ts() uint64        { return binary.LittleEndian.Uint64(f.buf[8:]) }
func (f *FlowCore) SrcIp() uint32     { return binary.LittleEndian.Uint32(f.buf[16:]) }
func (f *FlowCore) DstIp() uint32     { return binary.LittleEndian.Uint32(f.buf[20:]) }
func (f *FlowCore) SrcPort() uint16   { return binary.LittleEndian.Uint16(f.buf[24:]) }
func (f *FlowCore) DstPort() uint16   { return binary.LittleEndian.Uint16(f.buf[26:]) }
func (f *FlowCore) Proto() byte       { return f.buf[28] }
func (f *FlowCore) EventType() byte   { return f.buf[29] }
func (f *FlowCore) Packets() uint64   { return binary.LittleEndian.Uint64(f.buf[32:]) }
func (f *FlowCore) Bytes() uint64     { return binary.LittleEndian.Uint64(f.buf[40:]) }
func (f *FlowCore) FlowHash() uint64  { return binary.LittleEndian.Uint64(f.buf[48:]) }

// Raw returns the underlying byte slice for direct forwarding.
func (f *FlowCore) Raw() []byte { return f.buf[:FlowCoreSize] }
