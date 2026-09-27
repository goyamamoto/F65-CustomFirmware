#include "reset.h"
#include "clock.h"
#include "peripherals.h"
#include "ldo.h"
#include "watchdog.h"
#include "interrupts.h"
#include "usb.h"
#include "debug.h"
#include "console.h"
#include "matrix.h"
#include "utils.h"
#include "keyboard.h"
#include "user_init.h"
#include "indicators.h"
#include "kb.h"
#include "settings.h"
#include "tick.h"
#include "sleep.h"
#include "diag.h"
#ifdef DEBUG_SINK_UART
#    include "uart.h"
#endif
#ifdef RF_ENABLED
#    include "rf_controller.h"
#endif
#ifdef BOARD_USB_BOOT_GATE
// The board decides whether USB comes up at boot (a board whose saved transport
// is wireless keeps USB off, as its stock firmware does).
static bool usb_at_boot;
#endif

void init(void)
{
    reset_init();
    ldo_init();
    clock_init();
#ifdef BOOT_ESCAPE
    // Before anything else can hang: a board-defined key held at power-up jumps
    // to the ISP bootloader. It needs the clock already up.
    user_boot_escape();
#endif
    peripherals_init();
#ifdef DEBUG_SINK_UART
    uart_init();
#endif

    user_init();

    matrix_init();
    keyboard_init();
#ifdef BOARD_USB_BOOT_GATE
    usb_at_boot = kb_usb_at_boot();
    if (usb_at_boot) {
        usb_init();
    }
#else
    usb_init();
#endif
    indicators_init();

    tick_init();

    EA = 1;
}

static void restore_settings(void)
{
    if (!settings_load()) {
        indicators_apply_defaults();
    }
    indicators_validate_settings();
}

#ifdef RF_ENABLED
static void restore_rf_link(void)
{
    rf_set_link((rf_mode_t)user_settings.rf_link);

    keyboard_state.rf_link   = user_settings.rf_link;
    keyboard_state.connected = 1;
    keyboard_state.paired    = 1;
}
#endif

void main(void)
{
    init();

    dprintf("SMK v" TOSTRING(SMK_VERSION) "\r\n");
    dprintf("KB " KEYBOARD_NAME " / " LAYOUT_NAME "\r\n");
    dprintf("DEVICE vId:" TOSTRING(USB_VID) " pId:" TOSTRING(USB_PID) "\n\r");

    kb_init();

#ifdef RF_ENABLED
    rf_init();
#endif

    restore_settings();
#if DEBUG == 1
    settings_dump();
#endif

#ifdef BOARD_USB_BOOT_GATE
    if (usb_at_boot) {
        usb_wait_for_enumeration();
    }
#else
    usb_wait_for_enumeration();
#endif
    indicators_start();

#ifdef RF_ENABLED
    restore_rf_link();
#endif

    sleep_init(); // needs the board's GPIO and RF up

    while (1) {
        watchdog_kick();

        kb_update_switches();
        kb_update();
        matrix_task();

        indicators_render();

        usb_task();
        settings_task();
        sleep_task();

#if DEBUG == 1
        diag_task();
        interrupts_task();
        console_task();
#endif
    }
}
