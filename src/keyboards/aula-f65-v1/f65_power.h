#pragma once

#include <stdint.h>
#include <stdbool.h>

// Transport (RF_USB / RF_24G / RF_BT) and the Bluetooth slot (1..3).
uint8_t f65_transport(void);
uint8_t f65_bt_slot(void);
void    f65_load_link(uint8_t saved);

void f65_switch_tick(void); // every ~10 ms: the connection switch
void f65_sleep_tick(void);  // every ~10 ms: the sleep conditions
void f65_pair_tick(void);   // main loop: the 3 s pairing hold

// Fn+R (slot 0, 2.4 GHz) and Fn+Q/W/E (Bluetooth slots 1..3).
void f65_link_key(uint8_t slot, bool pressed);
void f65_pair_cancel(void);

void f65_sleep_wireless(void);
void f65_sleep_wired(void);
