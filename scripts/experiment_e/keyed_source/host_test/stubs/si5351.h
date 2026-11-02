#pragma once
#include <stdint.h>
enum { SI5351_CLK0, SI5351_CLK1, SI5351_CLK2 };
enum { SI5351_CRYSTAL_LOAD_8PF = 3, SI5351_PLL_INPUT_XO = 0 };
enum { SI5351_DRIVE_2MA, SI5351_DRIVE_4MA, SI5351_DRIVE_6MA, SI5351_DRIVE_8MA };
struct Si5351 {
  bool init(int,uint32_t,int32_t); uint8_t set_freq(uint64_t,int); void output_enable(int,uint8_t);
  void drive_strength(int,int); void set_correction(int32_t,int);
};
