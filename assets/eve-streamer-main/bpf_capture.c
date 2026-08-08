#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

struct flow_key {
    __u32 src_ip;
    __u32 dst_ip;
    __u16 src_port;
    __u16 dst_port;
    __u8  proto;
    __u8  pad[3];
};

struct flow_stats {
    __u64 pkts;
    __u64 bytes;
    __u64 last_seen;
    __u16 tcp_flags;
    __u8  pad[6];
};

// flow_core mirrors rfscythe.FlowCore in fb/flow.fbs.
// Field order and explicit padding guarantee the binary layout matches the
// FlatBuffers struct layout (56 bytes, little-endian):
//   [0]  flow_id    : u64
//   [8]  ts         : u64
//   [16] src_ip     : u32
//   [20] dst_ip     : u32
//   [24] src_port   : u16
//   [26] dst_port   : u16
//   [28] proto      : u8
//   [29] event_type : u8
//   [30] _pad[2]    : explicit alignment to offset 32
//   [32] packets    : u64
//   [40] bytes      : u64
//   [48] flow_hash  : u64 (FNV-1a of 5-tuple)
struct flow_core {
    __u64 flow_id;
    __u64 ts;
    __u32 src_ip;
    __u32 dst_ip;
    __u16 src_port;
    __u16 dst_port;
    __u8  proto;
    __u8  event_type;
    __u8  _pad[2];
    __u64 packets;
    __u64 bytes;
    __u64 flow_hash;
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 65536);
    __type(key, struct flow_key);
    __type(value, struct flow_stats);
} flow_table SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 22);
} rb SEC(".maps");

SEC("xdp")
int xdp_capture(struct xdp_md *ctx) {
    void *data_end = (void *)(long)ctx->data_end;
    void *data = (void *)(long)ctx->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return XDP_PASS;

    struct iphdr *ip = data + sizeof(*eth);
    if ((void *)(ip + 1) > data_end)
        return XDP_PASS;

    struct flow_key key = {};
    key.src_ip = ip->saddr;
    key.dst_ip = ip->daddr;
    key.proto = ip->protocol;

    __u16 current_flags = 0;

    if (ip->protocol == IPPROTO_TCP) {
        struct tcphdr *tcp = (void *)ip + sizeof(*ip);
        if ((void *)(tcp + 1) <= data_end) {
            key.src_port = bpf_ntohs(tcp->source);
            key.dst_port = bpf_ntohs(tcp->dest);
            // Extract flags (ACK, SYN, FIN, RST, etc.)
            current_flags = ((__u8 *)tcp)[13]; 
        }
    } else if (ip->protocol == IPPROTO_UDP) {
        struct udphdr *udp = (void *)ip + sizeof(*ip);
        if ((void *)(udp + 1) <= data_end) {
            key.src_port = bpf_ntohs(udp->source);
            key.dst_port = bpf_ntohs(udp->dest);
        }
    }

    struct flow_stats *stats;
    stats = bpf_map_lookup_elem(&flow_table, &key);

    // FNV-1a 64-bit hash of the 5-tuple for O(1) hypergraph edge lookup.
    __u64 hash = 14695981039346656037ULL;
    hash ^= key.src_ip;  hash *= 1099511628211ULL;
    hash ^= key.dst_ip;  hash *= 1099511628211ULL;
    hash ^= key.src_port; hash *= 1099511628211ULL;
    hash ^= key.dst_port; hash *= 1099511628211ULL;
    hash ^= key.proto;    hash *= 1099511628211ULL;

    if (!stats) {
        struct flow_stats new_stats = {1, data_end - data, bpf_ktime_get_ns(), current_flags};
        bpf_map_update_elem(&flow_table, &key, &new_stats, BPF_ANY);

        struct flow_core *e;
        e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
        if (e) {
            e->flow_id    = hash;
            e->ts         = bpf_ktime_get_ns();
            e->src_ip     = key.src_ip;
            e->dst_ip     = key.dst_ip;
            e->src_port   = key.src_port;
            e->dst_port   = key.dst_port;
            e->proto      = key.proto;
            e->event_type = 0; // FLOW_START
            e->_pad[0]    = 0;
            e->_pad[1]    = 0;
            e->packets    = 1;
            e->bytes      = data_end - data;
            e->flow_hash  = hash;
            bpf_ringbuf_submit(e, 0);
        }
    } else {
        __sync_fetch_and_add(&stats->pkts, 1);
        __sync_fetch_and_add(&stats->bytes, data_end - data);
        stats->last_seen = bpf_ktime_get_ns();
        stats->tcp_flags |= current_flags;

        // Emit on FIN/RST (flow end) or every 1024 packets (flow update).
        if ((current_flags & (0x01 | 0x04)) || (stats->pkts & 0x3FF) == 0) {
            struct flow_core *e;
            e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
            if (e) {
                __u8 ev_type = (current_flags & (0x01 | 0x04)) ? 2 : 1; // FLOW_END : FLOW_UPDATE
                e->flow_id    = hash;
                e->ts         = bpf_ktime_get_ns();
                e->src_ip     = key.src_ip;
                e->dst_ip     = key.dst_ip;
                e->src_port   = key.src_port;
                e->dst_port   = key.dst_port;
                e->proto      = key.proto;
                e->event_type = ev_type;
                e->_pad[0]    = 0;
                e->_pad[1]    = 0;
                e->packets    = stats->pkts;
                e->bytes      = stats->bytes;
                e->flow_hash  = hash;
                bpf_ringbuf_submit(e, 0);
            }

            if (current_flags & (0x01 | 0x04)) {
                bpf_map_delete_elem(&flow_table, &key);
            }
        }
    }

    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
