#include "kbdef.h"
#include "user_init.h"
#include "user_matrix.h"
#include "gpio.h"
#include "delay.h"
#include "isp.h"
#include "watchdog.h"

// EUART0 TXD/RXD are P5.5/P5.6, the radio module's link.
#ifdef DEBUG_SINK_UART
#    error "aula-f65-v1: DEBUG_SINK_UART is unusable here - EUART0 is wired to the radio module"
#endif

// The stock firmware's GPIO init (0xA57B-0xA5D5), value for value: latch,
// direction, pull-up, then drive strength. The latches are written first so no
// pin is driven to its reset level on the way. Result:
//   columns (P6.0-7, P5.0-2, P5.7, P4.0/2/3/5) and the LED-only columns P4.6 /
//   P7.4: outputs, high (idle)
//   LED anodes P1.0-5, P2.0-5, P3.0-5: outputs, low (dark)
//   rows P7.1-3, P5.3-4 and the unused P7.0: inputs, pulled up
//   P0.2 output high (radio TX line idle), P4.1 / P7.5 / P7.6 outputs low
//   P0.4 / P0.5 (connection switch), P0.3, P0.6, P0.7, P7.7: inputs, pulled up
//   P4.4, P4.7, P5.5, P5.6, P0.0, P0.1: inputs without pull-up
void user_gpio_init(void)
{
    GPIO_WRITE(0, 0x04);
    GPIO_WRITE(1, 0x00);
    GPIO_WRITE(2, 0x00);
    GPIO_WRITE(3, 0x00);
    GPIO_WRITE(4, 0x6D);
    GPIO_WRITE(5, 0x87);
    GPIO_WRITE(6, 0xFF);
    GPIO_WRITE(7, 0x10);

    GPIO_DIR_WRITE(0, 0x04);
    GPIO_DIR_WRITE(1, LED_P1_MASK);
    GPIO_DIR_WRITE(2, LED_P2_MASK);
    GPIO_DIR_WRITE(3, LED_P3_MASK);
    GPIO_DIR_WRITE(4, 0x6F);
    GPIO_DIR_WRITE(5, 0x87);
    GPIO_DIR_WRITE(6, 0xFF);
    GPIO_DIR_WRITE(7, 0x70);

    GPIO_PULLUP_WRITE(0, 0xF8);
    GPIO_PULLUP_WRITE(1, 0x3F);
    GPIO_PULLUP_WRITE(2, 0x3F);
    GPIO_PULLUP_WRITE(3, 0x3F);
    GPIO_PULLUP_WRITE(4, 0x6F);
    GPIO_PULLUP_WRITE(5, 0x9F);
    GPIO_PULLUP_WRITE(6, 0xFF);
    GPIO_PULLUP_WRITE(7, 0xDF);

    DRVCON = DRVCON_UNLOCK_P1;
    P1DRV  = GPIO_DRIVE_25MA;
    DRVCON = DRVCON_UNLOCK_P2;
    P2DRV  = GPIO_DRIVE_25MA;
    DRVCON = DRVCON_UNLOCK_P3;
    P3DRV  = GPIO_DRIVE_25MA;
    DRVCON = DRVCON_UNLOCK_P5;
    P5DRV  = GPIO_DRIVE_25MA;
    DRVCON = DRVCON_LOCK;
}

// The LED PWM is left untouched here: indicators_init() prepares the LED engine
// (led.c) and indicators_start() starts the PWM, the stock way (0x6313).
void user_init(void)
{
    user_gpio_init();
}

#define BOOT_ESCAPE_SAMPLES 16

// Holding the boot-escape key (BOOT_ESCAPE_KEY_COL / _ROW in kbdef.h: Esc)
// while the board powers up jumps straight to the ISP bootloader, before USB or
// the rest of the firmware runs. main() calls this right after the clock is up
// (the bootloader's 0xFF00 entry does not set the clock itself), so a bug later
// in init cannot lock out reflashing over USB. All samples (about 8 ms) must
// read pressed. The pins are left as the stock init sets them, which is also
// how the stock firmware enters the bootloader.
void user_boot_escape(void)
{
    user_gpio_init();
    user_matrix_col_select(BOOT_ESCAPE_KEY_COL);

    uint8_t pressed = 0;
    for (uint8_t i = 0; i < BOOT_ESCAPE_SAMPLES; i++) {
        watchdog_kick();
        delay_us(500);
        if (!(user_matrix_read_rows() & (1 << BOOT_ESCAPE_KEY_ROW))) {
            pressed++;
        }
    }

    user_matrix_col_deselect(BOOT_ESCAPE_KEY_COL);

    if (pressed == BOOT_ESCAPE_SAMPLES) {
        isp_jump();
    }
}
