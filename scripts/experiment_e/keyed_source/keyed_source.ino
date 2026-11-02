/*
 * Experiment E keyed source: Si5351 CLK0 + serial control. DRAFT, NOT RUN ON HARDWARE.
 *
 * Target: Arduino Uno/Nano class (ATmega328P, 16 MHz) + Etherkit Si5351 library (v2.x), I2C on A4/A5.
 * Purpose: gated CW probe for the Stage 1 lock-in (docs/EXPERIMENT_E_BENCH_PLAN.md).
 * Conducted use only: the output must go through a low-pass filter and attenuator
 * to the DUT (see the plan's "Stage 1 keyed source" section).
 *
 * Safety: boots with every output off and unarmed. RF only after "ARM 1". While armed,
 * a dead-man timer turns everything off if no command arrives for DEADMAN_MS
 * (the host should send "P" periodically during long runs).
 *
 * Serial 115200 8N1, one command per line, replies "OK ..." or "ERR ...".
 *   H                    help
 *   ?                    status (includes gate counters)
 *   ARM 1|0              arm / disarm (disarm turns all outputs off)
 *   F <hz>               set CLK0 frequency (MIN_HZ..MAX_HZ)
 *   D <2|4|6|8>          drive strength in mA
 *   E 0|1                base output state (used when not keying); needs ARM 1
 *   K <mHz> <duty%>      key output at <mHz> millihertz (100..20000), duty 1..99; "K 0" stops
 *   S <start> <stop> <step> <dwell_ms>   frequency sweep (keying continues separately)
 *   C <ppb>              XO correction (check the unit against your library version)
 *   V 0|1                verbose: log every gate edge and sweep step
 *   P                    ping (resets dead-man timer)
 *   X                    all off, disarm
 *
 * ---- Timing architecture (why it is built this way) ----
 * Three different moments are NOT the same thing and are kept separate:
 *   gate_requested        Timer1 ISR decides an edge is due. It toggles PIN_REF and pushes a
 *                         request {state, tick, TCNT1} into a ring. Resolution 1 cycle (62.5 ns)
 *                         in the timestamp, edge schedule exact to the 100 us tick. The ISR does
 *                         NO I2C, NO Serial, no division except the per-edge remainder step.
 *   i2c_command_completed loop() drains the ring, performs the Si5351 write, then raises PIN_MON.
 *                         Logged as done=. lat = done - req is the firmware's actual service delay.
 *   rf_gate_observed      NOT available in firmware. A successful I2C write does not tell you when
 *                         CLK0 amplitude changes (output stage settling, filter, attenuator).
 *                         Measure it externally (RF detector or reference receiver envelope on the
 *                         DAQ) and compare against PIN_REF / PIN_MON.
 * Gate log line (V 1):  GATE <seq> <0|1> req=<us> done=<us> lat=<us> c=<n>
 *   times are microseconds on the MCU clock (uint32, wraps at ~71.6 min; unwrap on the host).
 *   c = how many requests were serviced by this single I2C write (c>1 means loop() stalled across
 *   more than one edge and the edges were coalesced; the output reflects only the final state).
 * Status counters: edges, done, coalesced, ring_ovf (requests dropped: loop stalled for 8+ edges),
 *   log_drop, max_lat_us. Any nonzero ring_ovf means the run's gate timing is not trustworthy.
 *
 * Pins: PIN_REF (D8) follows the REQUESTED state (changed in the ISR). PIN_MON (D9) follows the
 * state after the I2C write has completed. Record both on the DAQ, plus the RF detector.
 *
 * Retunes during keying: an I2C retune (S sweep) is deferred if the next gate edge is within
 * GUARD_TICKS, so a long set_freq() cannot sit in front of an edge. F and C are not guarded.
 *
 * Host test: scripts/experiment_e/keyed_source/host_test/ builds this file with a simulated clock
 * and fails if any Wire/Si5351/Serial call happens inside the ISR. It does not prove AVR timing.
 *
 * Status: not flashed, not run with a real Si5351. Bench items: library version and
 * set_correction unit; measured lat distribution (PIN_REF vs PIN_MON on a scope); that CLK0 stays
 * off when unarmed; that Timer1 is not claimed by another library.
 */
#include <Wire.h>
#include "si5351.h"

Si5351 si5351;

const uint8_t  PIN_REF = 8;
const uint8_t  PIN_MON = 9;
const uint32_t MIN_HZ = 100000UL;
const uint32_t MAX_HZ = 30000000UL;
const uint32_t DEADMAN_MS = 30000UL;

const uint16_t CYC_PER_TICK = 1600;        // 16 MHz / 1600 = 10 kHz tick (100 us)
const uint32_t TICK_US = 100;
const uint32_t GUARD_TICKS = 50;           // 5 ms: no sweep retune this close to a gate edge
const uint8_t  REQ_N = 8, LOG_N = 16;

#ifdef EXP_E_HOST_TEST
  #include "host_hooks.h"                  // simulated pins, TCNT1, interrupt masking
#else
  #include <avr/interrupt.h>
  #define FAST_REF(on) do { if (on) PORTB |= _BV(0); else PORTB &= ~_BV(0); } while (0)   // D8
  #define FAST_MON(on) do { if (on) PORTB |= _BV(1); else PORTB &= ~_BV(1); } while (0)   // D9
  #define READ_TCNT1() TCNT1
#endif

bool     detected = false;
bool     armed = false;
bool     baseOn = false;      // output state when not keying
bool     outOn = false;       // CLK0 state after the last COMPLETED I2C write
bool     verbose = false;
uint32_t freqHz = 7074000UL;
uint8_t  driveMa = 2;

// ---- state shared with the ISR (volatile; 32-bit access guarded by noInterrupts in main) ----
volatile uint32_t tickCount = 0;
volatile bool     keying = false;
volatile bool     keyPhaseOn = false;
volatile uint32_t nextEdgeTick = 0;
volatile uint32_t keyMhz = 0, onNum = 0, offNum = 0, remAcc = 0;

struct Req { bool on; uint32_t tick; uint16_t cnt; };
volatile Req     reqRing[REQ_N];
volatile uint8_t reqHead = 0, reqTail = 0;      // ISR/main-producer writes head, main consumer moves tail
volatile uint16_t ringOvf = 0;
volatile uint32_t edgeSeq = 0;                  // requests accepted

struct Ev { uint32_t seq; bool on; uint32_t reqUs, doneUs; uint8_t c; };
Ev       evRing[LOG_N];
uint8_t  evHead = 0, evTail = 0;
uint16_t logDrop = 0;
uint32_t doneCount = 0, coalescedCount = 0, maxLatUs = 0, logSeq = 0;

bool     sweeping = false;
uint32_t swStop = 0, swStep = 0, swDwellMs = 0, swNextMs = 0;

uint32_t lastCmdMs = 0;
char     lineBuf[64];
uint8_t  lineLen = 0;

// ---- request path: callable from the ISR and (with interrupts masked) from main ----
static inline void pushRequest(bool on) {
  FAST_REF(on);                                   // PIN_REF = requested state, at the request instant
  uint8_t nh = (uint8_t)((reqHead + 1) % REQ_N);
  if (nh == reqTail) { ringOvf++; return; }       // full: loop() stalled for 8+ edges
  reqRing[reqHead].on = on;
  reqRing[reqHead].tick = tickCount;
  reqRing[reqHead].cnt = READ_TCNT1();
  reqHead = nh;
  edgeSeq++;
}

static inline void gateTick() {                   // runs in the Timer1 compare ISR, every 100 us
  tickCount++;
  if (keying && (int32_t)(tickCount - nextEdgeTick) >= 0) {
    keyPhaseOn = !keyPhaseOn;
    pushRequest(keyPhaseOn);
    // Exact period: ticks = (num + remainder) / mHz, carry the remainder (no drift, no rounding bias)
    uint32_t tot = (keyPhaseOn ? onNum : offNum) + remAcc;
    uint32_t t = tot / keyMhz;
    remAcc = tot % keyMhz;
    nextEdgeTick += (t ? t : 1);
  }
}

#ifndef EXP_E_HOST_TEST
ISR(TIMER1_COMPA_vect) { gateTick(); }
#endif

static uint32_t stampUs(uint32_t tick, uint16_t cnt) { return tick * TICK_US + (uint32_t)(cnt / (CYC_PER_TICK / TICK_US)); }

static void logEvent(uint32_t seq, bool on, uint32_t reqUs, uint32_t doneUs, uint8_t c) {
  uint8_t nh = (uint8_t)((evHead + 1) % LOG_N);
  if (nh == evTail) { logDrop++; return; }
  evRing[evHead] = Ev{seq, on, reqUs, doneUs, c};
  evHead = nh;
}

// Drain pending requests with ONE I2C write for the final state. Main context only.
static void serviceGate() {
  if (reqTail == reqHead) return;
  noInterrupts();
  uint8_t head = reqHead, tail = reqTail;
  Req snap[REQ_N]; uint8_t n = 0;
  for (uint8_t i = tail; i != head; i = (uint8_t)((i + 1) % REQ_N)) { snap[n].on = reqRing[i].on; snap[n].tick = reqRing[i].tick; snap[n].cnt = reqRing[i].cnt; n++; }
  reqTail = head;
  interrupts();
  bool want = snap[n - 1].on;
  if (want != outOn) si5351.output_enable(SI5351_CLK0, want ? 1 : 0);   // the only gate I2C write
  FAST_MON(want);
  outOn = want;
  noInterrupts();
  uint32_t dt = tickCount; uint16_t dc = READ_TCNT1();
  interrupts();
  uint32_t doneUs = stampUs(dt, dc);
  doneCount += n;
  if (n > 1) coalescedCount += n;
  for (uint8_t i = 0; i < n; i++) {
    uint32_t reqUs = stampUs(snap[i].tick, snap[i].cnt);
    uint32_t lat = doneUs - reqUs;
    if (lat > maxLatUs) maxLatUs = lat;
    logEvent(++logSeq, snap[i].on, reqUs, doneUs, n);
  }
}

// Change output from main context and wait until it is applied. Used for every non-keyed change
// and for every safety path, so "off" never depends on the ISR or on a later loop pass.
static void gateNow(bool on) {
  noInterrupts();
  pushRequest(on);
  interrupts();
  serviceGate();
}

static void flushLog() {
  while (evTail != evHead && Serial.availableForWrite() >= 56) {
    const Ev &e = evRing[evTail];
    if (verbose) {
      Serial.print(F("GATE ")); Serial.print(e.seq); Serial.print(' '); Serial.print(e.on ? 1 : 0);
      Serial.print(F(" req=")); Serial.print(e.reqUs);
      Serial.print(F(" done=")); Serial.print(e.doneUs);
      Serial.print(F(" lat=")); Serial.print(e.doneUs - e.reqUs);
      Serial.print(F(" c=")); Serial.println(e.c);
    }
    evTail = (uint8_t)((evTail + 1) % LOG_N);
  }
}

void allOff() {
  noInterrupts();
  keying = false;
  interrupts();
  sweeping = false;
  baseOn = false;
  armed = false;
  gateNow(false);
}

bool setFreq(uint32_t hz) {
  if (hz < MIN_HZ || hz > MAX_HZ) return false;
  si5351.set_freq((uint64_t)hz * 100ULL, SI5351_CLK0);  // library units: 0.01 Hz
  // Some library versions may touch the output-enable state in set_freq(); re-assert the last
  // completed state so a retune cannot turn the probe on in an off-phase or while unarmed.
  si5351.output_enable(SI5351_CLK0, outOn ? 1 : 0);
  freqHz = hz;
  return true;
}

bool setDrive(uint8_t ma) {
  switch (ma) {
    case 2: si5351.drive_strength(SI5351_CLK0, SI5351_DRIVE_2MA); break;
    case 4: si5351.drive_strength(SI5351_CLK0, SI5351_DRIVE_4MA); break;
    case 6: si5351.drive_strength(SI5351_CLK0, SI5351_DRIVE_6MA); break;
    case 8: si5351.drive_strength(SI5351_CLK0, SI5351_DRIVE_8MA); break;
    default: return false;
  }
  driveMa = ma;
  return true;
}

void printStatus() {
  Serial.print(F("OK armed=")); Serial.print(armed);
  Serial.print(F(" det=")); Serial.print(detected);
  Serial.print(F(" f=")); Serial.print(freqHz);
  Serial.print(F(" drive=")); Serial.print(driveMa);
  Serial.print(F(" base=")); Serial.print(baseOn);
  Serial.print(F(" out=")); Serial.print(outOn);
  Serial.print(F(" key=")); Serial.print(keying);
  Serial.print(F(" sweep=")); Serial.print(sweeping);
  Serial.print(F(" edges=")); Serial.print(edgeSeq);
  Serial.print(F(" done=")); Serial.print(doneCount);
  Serial.print(F(" coalesced=")); Serial.print(coalescedCount);
  Serial.print(F(" ring_ovf=")); Serial.print(ringOvf);
  Serial.print(F(" log_drop=")); Serial.print(logDrop);
  Serial.print(F(" max_lat_us=")); Serial.println(maxLatUs);
}

void help() {
  Serial.println(F("OK H ?  ARM 1|0  F hz  D 2|4|6|8  E 0|1  K mHz duty|K 0  S start stop step dwell_ms  C ppb  V 0|1  P  X"));
}

bool needArmed() {
  if (!armed) { Serial.println(F("ERR not armed")); return false; }
  return true;
}

void handle(char *line) {
  char *cmd = strtok(line, " ");
  if (!cmd) return;
  for (char *p = cmd; *p; ++p) if (*p >= 'a' && *p <= 'z') *p -= 32;
  char *a1 = strtok(NULL, " ");
  char *a2 = strtok(NULL, " ");
  char *a3 = strtok(NULL, " ");
  char *a4 = strtok(NULL, " ");

  if (!strcmp(cmd, "H")) { help(); }
  else if (!strcmp(cmd, "?")) { printStatus(); }
  else if (!strcmp(cmd, "P")) { Serial.println(F("OK")); }
  else if (!strcmp(cmd, "X")) { allOff(); Serial.println(F("OK off")); }
  else if (!strcmp(cmd, "V")) { verbose = a1 && a1[0] == '1'; Serial.println(F("OK")); }
  else if (!strcmp(cmd, "ARM")) {
    if (!a1) { Serial.println(F("ERR usage ARM 1|0")); }
    else if (a1[0] == '1') {
      if (!detected) { Serial.println(F("ERR si5351 not detected")); }
      else { armed = true; Serial.println(F("OK armed")); }
    } else { allOff(); Serial.println(F("OK disarmed")); }
  }
  else if (!strcmp(cmd, "F")) {
    if (!a1 || !setFreq(strtoul(a1, NULL, 10))) Serial.println(F("ERR freq out of range"));
    else Serial.println(F("OK"));
  }
  else if (!strcmp(cmd, "D")) {
    if (!a1 || !setDrive((uint8_t)atoi(a1))) Serial.println(F("ERR drive 2|4|6|8"));
    else Serial.println(F("OK"));
  }
  else if (!strcmp(cmd, "E")) {
    if (!needArmed()) return;
    baseOn = a1 && a1[0] == '1';
    if (!keying) gateNow(baseOn);
    Serial.println(F("OK"));
  }
  else if (!strcmp(cmd, "K")) {
    if (!a1) { Serial.println(F("ERR usage K mHz duty")); return; }
    uint32_t mhz = strtoul(a1, NULL, 10);
    if (mhz == 0) {
      noInterrupts(); keying = false; interrupts();
      gateNow(armed && baseOn);
      Serial.println(F("OK key off")); return;
    }
    if (!needArmed()) return;
    uint32_t duty = a2 ? strtoul(a2, NULL, 10) : 50;
    if (mhz < 100 || mhz > 20000 || duty < 1 || duty > 99) { Serial.println(F("ERR key range")); return; }
    noInterrupts(); keying = false; interrupts();                // stop the old schedule first
    gateNow(true);                                                // phase starts ON; this is edge 0
    noInterrupts();
    keyMhz = mhz;
    onNum = 100000UL * duty;                                      // ticks = 1e5*duty / mHz
    offNum = 100000UL * (100 - duty);
    uint32_t tot = onNum; remAcc = tot % mhz;
    nextEdgeTick = tickCount + tot / mhz;
    keyPhaseOn = true;
    keying = true;
    interrupts();
    Serial.println(F("OK key on"));
  }
  else if (!strcmp(cmd, "S")) {
    if (!needArmed()) return;
    if (!a1 || !a2 || !a3 || !a4) { Serial.println(F("ERR usage S start stop step dwell_ms")); return; }
    uint32_t start = strtoul(a1, NULL, 10);
    swStop = strtoul(a2, NULL, 10);
    swStep = strtoul(a3, NULL, 10);
    swDwellMs = strtoul(a4, NULL, 10);
    if (swStep == 0 || swDwellMs < 50 || start > swStop || start < MIN_HZ || swStop > MAX_HZ) {
      Serial.println(F("ERR sweep range")); return;
    }
    setFreq(start);
    sweeping = true;
    swNextMs = millis() + swDwellMs;
    Serial.print(F("STEP ")); Serial.print(start); Serial.print(' '); Serial.println(millis());
    Serial.println(F("OK sweep"));
  }
  else if (!strcmp(cmd, "C")) {
    if (!a1) { Serial.println(F("ERR usage C ppb")); return; }
    si5351.set_correction((int32_t)atol(a1), SI5351_PLL_INPUT_XO);
    setFreq(freqHz);
    Serial.println(F("OK"));
  }
  else { Serial.println(F("ERR unknown (H for help)")); }
}

void setup() {
  pinMode(PIN_REF, OUTPUT); pinMode(PIN_MON, OUTPUT);
  digitalWrite(PIN_REF, LOW); digitalWrite(PIN_MON, LOW);
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000);
  detected = si5351.init(SI5351_CRYSTAL_LOAD_8PF, 0, 0);
  if (detected) {
    si5351.output_enable(SI5351_CLK0, 0);
    si5351.output_enable(SI5351_CLK1, 0);
    si5351.output_enable(SI5351_CLK2, 0);
    setDrive(2);
    setFreq(freqHz);
  }
#ifndef EXP_E_HOST_TEST
  noInterrupts();
  TCCR1A = 0; TCCR1B = _BV(WGM12) | _BV(CS10);        // CTC, no prescale
  OCR1A = CYC_PER_TICK - 1; TCNT1 = 0; TIMSK1 = _BV(OCIE1A);
  interrupts();
#endif
  lastCmdMs = millis();
  Serial.println(detected ? F("READY unarmed, outputs off") : F("ERR si5351 not detected"));
}

void loop() {
  // 1. gate requests first: the I2C write for a pending edge has priority over everything else
  serviceGate();

  // 2. serial: non-blocking line assembly
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      lineLen = 0;
      lastCmdMs = millis();
      handle(lineBuf);
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;
      Serial.println(F("ERR line too long"));
    }
  }

  // 3. dead-man: no command for DEADMAN_MS while armed -> everything off
  if (armed && (millis() - lastCmdMs) > DEADMAN_MS) {
    allOff();
    Serial.println(F("DEADMAN off"));
  }

  // 4. sweep steps, deferred when a gate edge is imminent
  if (sweeping && (int32_t)(millis() - swNextMs) >= 0) {
    noInterrupts();
    bool near_ = keying && (int32_t)(nextEdgeTick - tickCount) < (int32_t)GUARD_TICKS;
    interrupts();
    if (!near_) {
      uint32_t next = freqHz + swStep;
      if (next > swStop) {
        sweeping = false;
        Serial.println(F("SWEEP DONE"));
      } else {
        setFreq(next);
        swNextMs += swDwellMs;
        if (verbose) { Serial.print(F("STEP ")); Serial.print(next); Serial.print(' '); Serial.println(millis()); }
      }
    }
  }

  // 5. log last: serial output never sits in front of a gate write
  serviceGate();
  flushLog();
}
