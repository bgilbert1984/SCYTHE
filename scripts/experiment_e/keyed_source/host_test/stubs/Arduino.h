#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
class __FlashStringHelper;
#define F(x) reinterpret_cast<const __FlashStringHelper*>(x)
void pinMode(uint8_t,uint8_t); void digitalWrite(uint8_t,uint8_t);
uint32_t millis(); uint32_t micros();
void noInterrupts(); void interrupts();
struct SerialT {
  void begin(long); int available(); int read(); int availableForWrite();
  void print(const char*); void print(const __FlashStringHelper*); void print(char); void print(int);
  void print(unsigned); void print(long); void print(unsigned long); void print(bool);
  void println(const char*); void println(const __FlashStringHelper*); void println(int); void println(unsigned);
  void println(unsigned long); void println(bool); void println();
};
extern SerialT Serial;
