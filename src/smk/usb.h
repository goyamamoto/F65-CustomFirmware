#pragma once

#include "report.h"
#include "usbhw.h"
#include <stdint.h>
#include <stdbool.h>

enum {
    USB_PROTOCOL_BOOT   = 0,
    USB_PROTOCOL_REPORT = 1,
};

void usb_init(void);
void usb_deinit(void);

void usb_send_report(__xdata report_keyboard_t *report);
void usb_send_nkro(__xdata report_nkro_t *report);
void usb_send_extra(__xdata report_extra_t *report);

bool    usb_is_configured(void);
uint8_t usb_device_state_get_protocol(void);

void usb_wait_for_enumeration(void);

// runs what the USB interrupt defers to the main loop: currently the jump into the ISP bootloader.
void usb_task(void);

// the part of the USB interrupt that does not vary by part; the vector calls it inside its own banking prologue.
void usb_irq_dispatch(void);

extern __bit usb_suspended;

#ifdef USB_ISP_PREPARE
// Board hook, run by usb_task() right before the report-5 ISP jump: stop what
// must not keep running under the bootloader (aula-f65-v1: the LED PWM).
void usb_isp_prepare(void);
#endif

#ifdef USB_WAKE_ON_KEY
// For a board that stays awake while the bus is suspended: call when a key goes
// down. If the host suspended the bus and enabled remote wakeup, this signals
// resume (USBCON.WKUP), once per suspend.
void usb_wake_host(void);
#endif

#if DEBUG == 1
bool usb_console_ready(void);
void usb_console_send(const __xdata uint8_t *data, uint8_t len);
#endif
