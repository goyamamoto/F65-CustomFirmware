#pragma once

#include "report.h"
#include <stdint.h>
#include <stdbool.h>

void kb_init();
void kb_send_report(__xdata report_keyboard_t *report);
void kb_send_nkro(__xdata report_nkro_t *report);
void kb_send_extra(__xdata report_extra_t *report);

bool kb_process_record(uint16_t keycode, bool key_pressed);
void kb_update_switches();
void kb_update();

#ifdef USJIS
// Called when the US-JIS mode actually changes (after any postponement).
void kb_usjis_mode_changed(bool enabled);
#endif

#ifdef KB_FN_LAYER_HOOK
// Called when the momentary (Fn) layer goes on or off, before the report that
// follows a release goes out.
void kb_fn_layer(bool held);
#endif

#ifdef BOARD_USB_BOOT_GATE
// Called from init() before usb_init(): false keeps USB off at boot.
bool kb_usb_at_boot(void);
#endif

#ifdef USB_INIT_GATE
// Called from usb_init(): false leaves USB alone (off).
bool kb_usb_allowed(void);
#endif

#ifdef APPLE_FN
// True for the momentary layer that stands for the Apple fn key (the Mac Fn layer).
bool kb_layer_is_apple_fn(uint8_t layer);
#endif
