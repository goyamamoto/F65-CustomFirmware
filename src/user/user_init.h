#pragma once

#include <stdint.h>

void user_init();

void user_gpio_init(void);

// Only on boards that set BOOT_ESCAPE: jumps to the ISP bootloader if the escape key is held.
void user_boot_escape(void);
