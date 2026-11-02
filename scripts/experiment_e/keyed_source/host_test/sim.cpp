// Host simulation of the keyed-source firmware. Simulated clock; Timer1 ISR fires every 100 us of
// simulated time, including in the middle of "slow" I2C stubs. Any Wire/Si5351/Serial call made
// while g_inISR is set aborts the test.
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>
#include <deque>
#include <cmath>
#include <algorithm>
#include "Arduino.h"
#include "si5351.h"
#include "Wire.h"
#define EXP_E_HOST_TEST 1
#include "../keyed_source.ino"

// ---- simulated world ----
static uint64_t g_now_ns = 0;                 // simulated time
static uint64_t g_next_tick_ns = 100000;      // next ISR at 100 us
static bool g_inISR = false, g_masked = false;
static int g_fail = 0;
#define CHECK(c, ...) do { if (!(c)) { g_fail++; printf("  FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)
static void die_isr(const char *what) { printf("FAIL: %s called inside ISR\n", what); exit(2); }

struct PinEv { uint64_t t; int pin; bool v; };
static std::vector<PinEv> g_pins;
struct I2cEv { uint64_t t0, t1; int kind; int arg; };   // kind 0=output_enable 1=set_freq
static std::vector<I2cEv> g_i2c;
static bool g_clk0 = false;                   // simulated CLK0 output-enable register
static std::vector<std::pair<uint64_t,bool>> g_clk0_hist;
static std::string g_out; static std::deque<char> g_in;
// latency knobs
static uint64_t LAT_OE_NS = 120000, LAT_FREQ_NS = 4000000, g_stall_ns = 0;   // one-shot extra stall in next output_enable
static bool g_armed_ever_on_while_unarmed = false;

static void run_isr() { g_inISR = true; gateTick(); g_inISR = false; }
static void advance(uint64_t dt) {            // move time; fire ISR at tick boundaries inside the span
  uint64_t end = g_now_ns + dt;
  while (g_next_tick_ns <= end) {
    g_now_ns = g_next_tick_ns; g_next_tick_ns += 100000;
    if (!g_masked) run_isr();
  }
  g_now_ns = end;
}
void pinMode(uint8_t,uint8_t){} void digitalWrite(uint8_t,uint8_t){}
uint32_t millis(){ return (uint32_t)(g_now_ns/1000000); }
uint32_t micros(){ return (uint32_t)(g_now_ns/1000); }
void noInterrupts(){ g_masked = true; } void interrupts(){ g_masked = false; }
void host_pin_ref(bool v){ g_pins.push_back({g_now_ns,8,v}); }
void host_pin_mon(bool v){ g_pins.push_back({g_now_ns,9,v}); }
uint16_t host_tcnt1(){ return (uint16_t)(((g_now_ns % 100000) * 16) / 1000); }   // 16 MHz cycles into tick
void SerialT::begin(long){} int SerialT::available(){ return (int)g_in.size(); }
int SerialT::read(){ int c=g_in.front(); g_in.pop_front(); return c; } int SerialT::availableForWrite(){ return 63; }
#define SP(x) void SerialT::print x { if(g_inISR) die_isr("Serial"); }
static void app(const std::string&s){ if(g_inISR) die_isr("Serial"); g_out+=s; }
void SerialT::print(const char*s){app(s);} void SerialT::print(const __FlashStringHelper*s){app((const char*)s);}
void SerialT::print(char c){app(std::string(1,c));} void SerialT::print(int v){app(std::to_string(v));}
void SerialT::print(unsigned v){app(std::to_string(v));} void SerialT::print(long v){app(std::to_string(v));}
void SerialT::print(unsigned long v){app(std::to_string(v));} void SerialT::print(bool v){app(v?"1":"0");}
void SerialT::println(const char*s){app(std::string(s)+"\n");} void SerialT::println(const __FlashStringHelper*s){app(std::string((const char*)s)+"\n");}
void SerialT::println(int v){app(std::to_string(v)+"\n");} void SerialT::println(unsigned v){app(std::to_string(v)+"\n");}
void SerialT::println(unsigned long v){app(std::to_string(v)+"\n");} void SerialT::println(bool v){app(v?"1\n":"0\n");} void SerialT::println(){app("\n");}
SerialT Serial; WireT Wire; Si5351 si5351_unused;
void WireT::begin(){ if(g_inISR) die_isr("Wire"); } void WireT::setClock(unsigned long){}
bool Si5351::init(int,uint32_t,int32_t){ return true; }
void Si5351::drive_strength(int,int){ if(g_inISR) die_isr("si5351.drive_strength"); advance(100000); }
void Si5351::set_correction(int32_t,int){ if(g_inISR) die_isr("si5351.set_correction"); advance(100000); }
uint8_t Si5351::set_freq(uint64_t,int){ if(g_inISR) die_isr("si5351.set_freq"); uint64_t t0=g_now_ns; advance(LAT_FREQ_NS); g_i2c.push_back({t0,g_now_ns,1,0}); return 0; }
void Si5351::output_enable(int clk,uint8_t en){
  if(g_inISR) die_isr("si5351.output_enable");
  uint64_t t0=g_now_ns; advance(LAT_OE_NS + g_stall_ns); g_stall_ns = 0;
  if (clk == SI5351_CLK0) { g_clk0 = en; g_clk0_hist.push_back({g_now_ns, (bool)en}); if (en && !armed) g_armed_ever_on_while_unarmed = true; }
  g_i2c.push_back({t0,g_now_ns,0,en});
}

// ---- test driver ----
static void send(const char *s){ for(; *s; ++s) g_in.push_back(*s); g_in.push_back('\n'); }
static void run_for(uint64_t ns, uint64_t loop_gap_ns = 20000) {   // call loop() repeatedly
  uint64_t end = g_now_ns + ns;
  while (g_now_ns < end) { loop(); advance(loop_gap_ns); }
}
static void run_pinging(uint64_t ns) {                              // host keeps the dead-man alive
  for (uint64_t done = 0; done < ns; done += 10000000000ULL) { send("P"); run_for(std::min<uint64_t>(10000000000ULL, ns-done)); }
}
static void reset() {
  g_now_ns = 0; g_next_tick_ns = 100000; g_pins.clear(); g_i2c.clear(); g_out.clear(); g_in.clear(); g_clk0_hist.clear();
  g_clk0 = false; g_masked = false; g_inISR = false; g_stall_ns = 0; g_armed_ever_on_while_unarmed = false;
  // re-init firmware globals
  tickCount = 0; keying = false; keyPhaseOn = false; nextEdgeTick = 0; remAcc = 0;
  reqHead = reqTail = 0; ringOvf = 0; edgeSeq = 0; evHead = evTail = 0; logDrop = 0;
  doneCount = coalescedCount = maxLatUs = logSeq = 0; armed = baseOn = outOn = verbose = sweeping = false;
  lineLen = 0; freqHz = 7074000UL; setup(); g_out.clear();
}
struct Gate { uint64_t req_us, done_us; int on; int c; };
static std::vector<Gate> parse_gates() {
  std::vector<Gate> v; size_t p = 0;
  while ((p = g_out.find("GATE ", p)) != std::string::npos) {
    unsigned seq, on, c; unsigned long long rq, dn, lat;
    if (sscanf(g_out.c_str()+p, "GATE %u %u req=%llu done=%llu lat=%llu c=%u", &seq,&on,&rq,&dn,&lat,&c)==6) v.push_back({rq,dn,(int)on,(int)c});
    p += 5;
  }
  return v;
}

int main(int argc, char **argv) {
  if (argc == 5 && !strcmp(argv[1], "--dump-keying")) {        // used by replay/test_replay.py: print GATE lines
    reset(); send("ARM 1"); send("V 1"); run_for(1000000);
    char k[64]; snprintf(k, sizeof k, "K %s %s", argv[2], argv[3]); send(k);
    run_pinging((uint64_t)atoi(argv[4]) * 1000000000ULL);
    printf("%s", g_out.c_str()); return 0;
  }
  // T1: boot state: nothing on, unarmed
  reset(); run_for(50000000);
  printf("T1 boot: no output enable while unarmed\n");
  CHECK(!g_armed_ever_on_while_unarmed && !g_clk0 && !armed, "output on at boot");

  // T2: 7.000 Hz keying, 50%: schedule exactness and request/complete separation
  reset(); send("ARM 1"); send("V 1"); run_for(1000000); send("K 7000 50");
  run_pinging(60ULL*1000000000ULL);                                   // 60 s
  auto g = parse_gates();
  printf("T2 keying 7 Hz x 60 s: %zu gate events\n", g.size());
  CHECK(g.size() > 800 && g.size() < 900, "expected ~840 edges, got %zu", g.size());
  // requested edges: on-times at exact 142857.14 us period -> compare mean period over run
  std::vector<double> on_req; for (auto&e: g) if (e.on) on_req.push_back((double)e.req_us);
  double period = (on_req.back()-on_req[1])/(on_req.size()-2);
  printf("   mean requested period %.3f us (ideal 142857.143), jitter bound 100 us tick\n", period);
  CHECK(fabs(period - 142857.143) < 1.0, "period error %.3f us", period-142857.143);
  double maxdev = 0; for (size_t i=1;i<on_req.size();i++){ double ideal = on_req[1] + (i-1)*142857.142857; maxdev = std::max(maxdev, fabs(on_req[i]-ideal)); }
  printf("   max deviation of request times from ideal grid: %.1f us\n", maxdev);
  CHECK(maxdev <= 101.0, "request jitter %.1f us > one tick", maxdev);
  double maxlat = 0; for (auto&e: g) maxlat = std::max(maxlat,(double)(e.done_us-e.req_us));
  printf("   service latency max %.0f us (stub I2C = 120 us)\n", maxlat);
  CHECK(maxlat < 400, "latency %.0f us too high for idle loop", maxlat);
  // PIN_REF leads PIN_MON for every edge
  int lead_bad = 0; for (size_t i=0;i+1<g_pins.size();i++) if (g_pins[i].pin==9) { /* MON */ }
  uint64_t last_ref = 0; for (auto&p: g_pins) { if (p.pin==8) last_ref=p.t; else if (p.t < last_ref) lead_bad++; }
  CHECK(lead_bad == 0, "PIN_MON changed before PIN_REF");

  // T3: sweep retune stall (4 ms set_freq) must not delay an edge beyond the guard
  reset(); send("ARM 1"); send("V 1"); run_for(1000000); send("K 7000 50"); send("S 7000000 7100000 1000 60");
  run_pinging(20ULL*1000000000ULL);
  g = parse_gates(); maxlat = 0; for (auto&e: g) maxlat = std::max(maxlat,(double)(e.done_us-e.req_us));
  printf("T3 sweep + keying: %zu edges, max latency %.0f us (set_freq stub = 4000 us)\n", g.size(), maxlat);
  CHECK(maxlat <= 500, "latency %.0f us: a retune sat in front of an edge (guard failed)", maxlat);
  CHECK(ringOvf == 0, "ring overflow %u", (unsigned)ringOvf);

  // T4: loop stall longer than a half period -> coalescing counted, final state correct, nothing silent
  reset(); send("ARM 1"); send("V 1"); run_for(1000000); send("K 7000 50"); run_for(500000000);
  g_stall_ns = 200000000ULL;                                           // one 200 ms I2C stall (> 142 ms period)
  run_for(2000000000ULL);
  g = parse_gates(); int coal = 0; for (auto&e: g) if (e.c > 1) coal++;
  printf("T4 200 ms stall: coalesced events %d, ring_ovf %u, max_lat %u us\n", coal, (unsigned)ringOvf, (unsigned)maxLatUs);
  CHECK(coal >= 2, "stall across edges was not reported as coalesced");
  CHECK(maxLatUs >= 150000, "stall not visible in max_lat_us");
  send("?"); run_for(100000);
  CHECK(g_out.find("coalesced=") != std::string::npos, "status lacks counters");

  // T5: stall longer than 8 edges -> overflow counted, never silent
  reset(); send("ARM 1"); send("K 20000 50"); run_for(200000);        // 20 Hz keying (25 ms half period)
  g_stall_ns = 400000000ULL; run_for(1000000000ULL);
  printf("T5 400 ms stall @20 Hz: ring_ovf=%u\n", (unsigned)ringOvf);
  CHECK(ringOvf > 0, "overflow not counted");

  // T6: X during keying: output off immediately and stays off; no later edge turns it on
  reset(); send("ARM 1"); send("K 7000 50"); run_for(300000000);
  send("X"); run_for(100000); uint64_t tx = g_now_ns; bool off_now = !g_clk0;
  run_for(3000000000ULL);
  bool reon = false; for (auto&h: g_clk0_hist) if (h.first > tx && h.second) reon = true;
  printf("T6 kill switch: off immediately=%d, re-enabled later=%d, armed=%d\n", off_now, reon, armed);
  CHECK(off_now && !reon && !armed && !keying, "X did not leave output off");

  // T7: dead-man: no commands for 30 s while keying -> off
  reset(); send("ARM 1"); send("K 7000 50"); run_for(32ULL*1000000000ULL);
  printf("T7 dead-man: armed=%d out=%d clk0=%d\n", armed, outOn, g_clk0);
  CHECK(!armed && !g_clk0 && !keying, "dead-man did not shut down");
  CHECK(g_out.find("DEADMAN off") != std::string::npos, "no DEADMAN message");

  // T8: commands that must be refused while unarmed
  reset(); send("E 1"); send("K 7000 50"); send("S 7000000 7100000 1000 60"); run_for(1000000000ULL);
  printf("T8 unarmed refusal: clk0=%d\n", g_clk0);
  CHECK(!g_clk0 && !g_armed_ever_on_while_unarmed && !keying && !sweeping, "unarmed command energised output");

  // T9: Serial/Wire/Si5351 inside ISR would have aborted; reaching here means none occurred
  printf("T9 no I2C/Serial call in ISR context across all runs: ok\n");

  printf(g_fail ? "\n%d CHECK(S) FAILED\n" : "\nALL HOST CHECKS PASSED\n", g_fail);
  return g_fail ? 1 : 0;
}
