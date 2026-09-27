#pragma once

#include <stdint.h>

// The keyboard's realtime work, driven by the platform's periodic tick.
//
// One hardware timer has to serve two jobs that can't overlap: sweeping the key
// matrix, and emitting LED PWM subframes (LED drive current couples into the row
// sense). So ticks alternate between them, and this is where that interleave is
// decided.

void tick_init(void);

void tick_dispatch(void);

// Matrix scans since boot: the time base for anything that needs real time, as
// there is no millisecond clock. The rate depends on the board and its load:
// an Air75 in wireless mode measured ~1.4 ms per scan (4400 scans ~ 6 s).
uint16_t tick_scans(void);

void tick_pause(void);
void tick_resume(void);
