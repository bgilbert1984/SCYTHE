/*
 * Experiment E keyed source: Si5351 CLK0 + serial control. DRAFT, NOT RUN ON HARDWARE.
 *
 * Target: Arduino Uno/Nano class + Etherkit Si5351 library (v2.x), I2C on A4/A5.
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
 *   ?                    status
 *   ARM 1|0              arm / disarm (disarm turns all outputs off)
 *   F <hz>               set CLK0 frequency (MIN_HZ..MAX_HZ)
 *   D <2|4|6|8>          drive strength in mA
 *   E 0|1                base output state (used when not keying); needs ARM 1
 *   K <mHz> <duty%>      key output at <mHz> millihertz (100..20000), duty 1..99; "K 0" stops
 *   S <start> <stop> <step> <dwell_ms>   frequency sweep (keying continues separately)
 *   C <ppb>              XO correction (check the unit against your library version)
 *   V 0|1                verbose edge/step logging
 *   P                    ping (resets dead-man timer)
 *   X                    all off, disarm
 *
 * Pins: PIN_REF goes high/low in the same loop pass immediately BEFORE the I2C write that
 * changes CLK0; PIN_MON follows after the write completes. Record both on the DAQ as the
 * lock-in reference and as the actual-gate monitor. Edge timing is scheduled with
 * micros() in loop(), so jitter is the loop latency (keep loop() non-blocking).
 *
 * Status: syntax-checked on a host with stub headers only. Not flashed, not run with a
 * real Si5351. Bench items to verify: library version and set_correction unit, I2C timing
 * of the gate edge (PIN_REF vs PIN_MON on a scope), and that CLK0 stays off when unarmed.
 */
#include <Wire.h>
#include "si5351.h"

Si5351 si5351;

const uint8_t  PIN_REF = 8;
const uint8_t  PIN_MON = 9;
const uint32_t MIN_HZ = 100000UL;
const uint32_t MAX_HZ = 30000000UL;
const uint32_t DEADMAN_MS = 30000UL;

bool     detected = false;
bool     armed = false;
bool     baseOn = false;      // output state when not keying
bool     outOn = false;       // actual CLK0 state
bool     verbose = false;
uint32_t freqHz = 7074000UL;
uint8_t  driveMa = 2;

bool     keying = false;
uint32_t keyOnUs = 0, keyOffUs = 0, nextEdgeUs = 0;
bool     keyPhaseOn = false;

bool     sweeping = false;
uint32_t swStop = 0, swStep = 0, swDwellMs = 0, swNextMs = 0;

uint32_t lastCmdMs = 0;
char     lineBuf[64];
uint8_t  lineLen = 0;

void setOutput(bool on) {
  digitalWrite(PIN_REF, on ? HIGH : LOW);
  si5351.output_enable(SI5351_CLK0, on ? 1 : 0);
  digitalWrite(PIN_MON, on ? HIGH : LOW);
  outOn = on;
  if (verbose) { Serial.print(F("EDGE ")); Serial.print(micros()); Serial.print(' '); Serial.println(on ? 1 : 0); }
}

void allOff() {
  keying = false;
  sweeping = false;
  baseOn = false;
  armed = false;
  setOutput(false);
}

bool setFreq(uint32_t hz) {
  if (hz < MIN_HZ || hz > MAX_HZ) return false;
  si5351.set_freq((uint64_t)hz * 100ULL, SI5351_CLK0);  // library units: 0.01 Hz
  // Some library versions may touch the output-enable state in set_freq(); re-assert ours
  // so a retune can never turn the probe on during a keyed off-phase or while unarmed.
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
  Serial.print(F(" sweep=")); Serial.println(sweeping);
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
    if (!keying) setOutput(baseOn);
    Serial.println(F("OK"));
  }
  else if (!strcmp(cmd, "K")) {
    if (!a1) { Serial.println(F("ERR usage K mHz duty")); return; }
    uint32_t mhz = strtoul(a1, NULL, 10);
    if (mhz == 0) { keying = false; setOutput(armed && baseOn); Serial.println(F("OK key off")); return; }
    if (!needArmed()) return;
    uint32_t duty = a2 ? strtoul(a2, NULL, 10) : 50;
    if (mhz < 100 || mhz > 20000 || duty < 1 || duty > 99) { Serial.println(F("ERR key range")); return; }
    uint32_t periodUs = 1000000000UL / mhz;
    keyOnUs = (uint32_t)(((uint64_t)periodUs * duty) / 100ULL);
    keyOffUs = periodUs - keyOnUs;
    keyPhaseOn = true;
    keying = true;
    setOutput(true);
    nextEdgeUs = micros() + keyOnUs;
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
  lastCmdMs = millis();
  Serial.println(detected ? F("READY unarmed, outputs off") : F("ERR si5351 not detected"));
}

void loop() {
  // serial: non-blocking line assembly
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

  // dead-man: no command for DEADMAN_MS while armed -> everything off
  if (armed && (millis() - lastCmdMs) > DEADMAN_MS) {
    allOff();
    Serial.println(F("DEADMAN off"));
  }

  // keying edges
  if (keying && (int32_t)(micros() - nextEdgeUs) >= 0) {
    keyPhaseOn = !keyPhaseOn;
    setOutput(keyPhaseOn);
    nextEdgeUs += keyPhaseOn ? keyOnUs : keyOffUs;
  }

  // sweep steps
  if (sweeping && (int32_t)(millis() - swNextMs) >= 0) {
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
