kb_dir = meson.current_source_dir()

keyboards += {
    'aula-f65-v1' : {
        'dir' : kb_dir,
        'platform' : 'sh68f90',
        'src' : files(
            'user_matrix.c',
            'user_init.c',
            'user_sleep.c',
            'kb.c',
            'f65_rf.c',
            'f65_power.c',
            'led.c',
        ),
        'layouts' : {
            # Plain US ANSI, as printed.
            'ansi' : {
                'dir' : kb_dir / 'layouts/ansi',
                'src' : files('layouts/ansi/layout.c', 'indicators.c', 'backlight.c'),
                'defines' : ['F65_LIGHTING=1', 'SETTINGS_F65_LIGHTING=1'],
            },
            # ANSI plus US-JIS (Fn+Tab), IME mod-taps on both sides of Space (Fn and Right Ctrl swapped), Caps Lock / Left Ctrl swapped.
            'usjis' : {
                'dir' : kb_dir / 'layouts/usjis',
                'src' : files('layouts/usjis/layout.c', 'indicators.c', 'backlight.c'),
                'defines' : ['USJIS=1', 'TAP_HOLD=1', 'F65_LIGHTING=1', 'SETTINGS_F65_LIGHTING=1'],
            },
            # ansi and usjis with VIA: the keymap is edited from VIA (raw HID on
            # interface 0) and kept in flash; via.json is the
            # definition to load into VIA.
            'ansi-via' : {
                'dir' : kb_dir / 'layouts/ansi',
                'src' : files('layouts/ansi/layout.c', 'indicators.c', 'backlight.c'),
                # TAP_HOLD: mod-taps (MT) and layer-taps (LT) that VIA sets work here too;
                # MOUSEKEY_ENABLE: so do the mouse keys
                'defines' : ['TAP_HOLD=1', 'F65_LIGHTING=1', 'SETTINGS_F65_LIGHTING=1', 'KB_KEYMAP_CHANGED_HOOK=1', 'MOUSEKEY_ENABLE=1'],
                'via' : true,
            },
            'usjis-via' : {
                'dir' : kb_dir / 'layouts/usjis',
                'src' : files('layouts/usjis/layout.c', 'indicators.c', 'backlight.c'),
                'defines' : ['USJIS=1', 'TAP_HOLD=1', 'F65_LIGHTING=1', 'SETTINGS_F65_LIGHTING=1', 'KB_KEYMAP_CHANGED_HOOK=1', 'MOUSEKEY_ENABLE=1'],
                'via' : true,
            },
            # For the first hardware check of the LEDs: ansi, no indicators, one
            # channel of one cell lit dimly at a time (led_diag.c).
            'leddiag' : {
                'dir' : kb_dir / 'layouts/leddiag',
                'src' : files('layouts/leddiag/layout.c', 'led_diag.c'),
                'defines' : ['LED_DIAG=1'],
            },
        },
        'options' : {
            # The original USB ID, so `sinowisp -d aula-f75` (258A:010C, interface 1, report 5) finds it.
            'vendor_id' : '0x258a',
            'product_id' : '0x010c',
            'defines' : [
                'BOOT_ESCAPE=1',
                'SETTINGS_OS_MODE=1',
                'NKRO_DEFAULT_OFF=1',
                'LATCH_KEYCODES=1',
                'USB_WAKE_ON_KEY=1',
                'USB_FEATURE_REPORT_STRICT=1',
                'RF_EUART0=1',
                'PWM4_MS_TICK=1',
                'BOARD_USB_BOOT_GATE=1',
                'KB_FN_LAYER_HOOK=1',
                'CONSUMER_USAGE_MAX=0x02FF',
                'USB_INIT_GATE=1',
                'USB_IRQ_ACTIVITY=1',
                'POWER_INT4_WOKE_API=1',
                'DELAY_NO_WATCHDOG_KICK=1',
                'USB_ISP_PREPARE=1',  # led.c: LEDs off before the report-5 ISP jump
                'STARTUP_LED_OFF=1',  # startup.c: PWM and anode ports back to their reset state first
                'MATRIX_DEBOUNCE_SCANS=4',  # matrix.c: a key change must hold 4 scans (~5.7 ms at a scan every ~1.4 ms)
                'LED_SUBFRAMES_PER_SCAN=5',  # tick.c: five LED subframes between two scans (led.c stops the banks before each)
                'SYSTICK_LED_SUBFRAME_RELOAD=0xFE6F',  # systick.c: 65536 - 401, a 200 us subframe: 20 slots in ~5.7 ms, ~175 Hz per LED
                'FLASH_OP_WATCHDOG_KICK=1',  # flash.c: a watchdog kick before each erase / program
                'CLOCK_PLL_WAIT_BOUND=20000',  # clock.c: the PLL lock wait gives up after 20000 polls and switches anyway
            ],
            # sinowisp has no aula-f65-v1 entry; aula-f75 is the same ID and ISP report.
            'sinowisp_device' : 'aula-f75',
            'manufacturer' : 'SMK',
            'product' : 'AULA F65 (SMK)',
        },
    },
}
