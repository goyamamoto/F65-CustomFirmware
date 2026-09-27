kb_dir = meson.current_source_dir()

keyboards += {
    'nuphy-air75' : {
        'dir' : kb_dir,
        'platform' : 'sh68f90',
        'src' : files(
            'user_matrix.c',
            'user_init.c',
            'user_sleep.c',
            'kb.c',
            'indicators.c',
        ),
        'layouts' : {
            # Plain US ANSI.
            'ansi' : {
                'dir' : kb_dir / 'layouts/ansi',
                'src' : files('layouts/ansi/layout.c'),
            },
            # ANSI plus US-JIS (Fn+Tab), IME mod-taps beside Space and Caps Lock / Left Ctrl swapped.
            'usjis' : {
                'dir' : kb_dir / 'layouts/usjis',
                'src' : files('layouts/usjis/layout.c'),
                'defines' : ['USJIS=1', 'TAP_HOLD=1'],
            },
        },
        'options' : {
            'vendor_id' : '0x05ac',
            'product_id' : '0x024f',
            'wireless' : 'bk3632',
            'defines' : ['BOOT_ESCAPE=1', 'APPLE_FN=1', 'RF_BT_NAME="Air75"'],
            'manufacturer' : 'SMK',
            'product' : 'Air75 (SMK)',
            'led_rows' : '6',
            'led_cols' : '16',
            'led_cx2' : '15',
            'led_cy2' : '5',
        },
    },
}
