// SPDX-License-Identifier: GPL-2.0-or-later
// VIA's raw HID commands, after QMK's quantum/via.c (raw_hid_receive),
// Copyright 2019 Jason Williams (Wilba). Only the keymap is kept: no macros,
// no custom values (lighting menus), no encoders.

#include "via.h"
#include "dynamic_keymap.h"
#include "matrix.h"
#include "tick.h"
#include "kbdef.h"
#include <stdint.h>

enum via_command_id {
    id_get_protocol_version                 = 0x01,
    id_get_keyboard_value                   = 0x02,
    id_set_keyboard_value                   = 0x03,
    id_dynamic_keymap_get_keycode           = 0x04,
    id_dynamic_keymap_set_keycode           = 0x05,
    id_dynamic_keymap_reset                 = 0x06,
    id_dynamic_keymap_macro_get_count       = 0x0C,
    id_dynamic_keymap_macro_get_buffer_size = 0x0D,
    id_dynamic_keymap_macro_get_buffer      = 0x0E,
    id_dynamic_keymap_macro_set_buffer      = 0x0F,
    id_dynamic_keymap_macro_reset           = 0x10,
    id_dynamic_keymap_get_layer_count       = 0x11,
    id_dynamic_keymap_get_buffer            = 0x12,
    id_dynamic_keymap_set_buffer            = 0x13,
    id_unhandled                            = 0xFF,
};

enum via_keyboard_value_id {
    id_uptime              = 0x01,
    id_layout_options      = 0x02,
    id_switch_matrix_state = 0x03,
    id_firmware_version    = 0x04,
    id_device_indication   = 0x05,
};

// The most a 32-byte report carries after the command, offset and size.
#define VIA_BUFFER_CHUNK 28u

static void put_u32(__xdata uint8_t *p, uint16_t hi, uint16_t lo)
{
    p[0] = (uint8_t)(hi >> 8);
    p[1] = (uint8_t)hi;
    p[2] = (uint8_t)(lo >> 8);
    p[3] = (uint8_t)lo;
}

#ifdef VIA_MS32
uint32_t VIA_MS32(void); // the board's milliseconds since power-up
#endif

static void get_keyboard_value(__xdata uint8_t *data)
{
    __xdata uint8_t *command_data = data + 1;

    switch (command_data[0]) {
        case id_uptime:
#ifdef VIA_MS32
        {
            const uint32_t ms = VIA_MS32(); // since power-up
            put_u32(&command_data[1], (uint16_t)(ms >> 16), (uint16_t)ms);
        }
#else
            put_u32(&command_data[1], 0, tick_scans()); // no millisecond clock: the scans
#endif
        break;

        case id_layout_options:
            put_u32(&command_data[1], 0, 0);
            break;

        case id_switch_matrix_state: {
            // two bytes a row (MATRIX_COLS <= 16), from row command_data[1]
            const uint8_t offset = command_data[1];
            uint8_t       i      = 2;
            for (uint8_t row = 0; row < VIA_BUFFER_CHUNK / 2u && (uint8_t)(row + offset) < MATRIX_ROWS; row++) {
                const uint16_t value = matrix_get_row((uint8_t)(row + offset));
                command_data[i++]    = (uint8_t)(value >> 8);
                command_data[i++]    = (uint8_t)value;
            }
            break;
        }

        case id_firmware_version:
            put_u32(&command_data[1], 0, 0);
            break;

        default:
            data[0] = id_unhandled;
            break;
    }
}

void via_process(__xdata uint8_t *data)
{
    __xdata uint8_t *command_data = data + 1;

    switch (data[0]) {
        case id_get_protocol_version:
            command_data[0] = (uint8_t)(VIA_PROTOCOL_VERSION >> 8);
            command_data[1] = (uint8_t)VIA_PROTOCOL_VERSION;
            break;

        case id_get_keyboard_value:
            get_keyboard_value(data);
            break;

        case id_set_keyboard_value:
            // layout options and the device indication (VIA's "identify"
            // blink) are taken and have no effect
            if (command_data[0] != id_layout_options && command_data[0] != id_device_indication) {
                data[0] = id_unhandled;
            }
            break;

        case id_dynamic_keymap_get_keycode: {
            const uint16_t keycode = dynamic_keymap_keycode(command_data[0], command_data[1], command_data[2]);
            command_data[3]        = (uint8_t)(keycode >> 8);
            command_data[4]        = (uint8_t)keycode;
            break;
        }

        case id_dynamic_keymap_set_keycode:
            dynamic_keymap_set_keycode(command_data[0], command_data[1], command_data[2], (uint16_t)((uint16_t)command_data[3] << 8 | command_data[4]));
            break;

        case id_dynamic_keymap_reset:
            dynamic_keymap_reset();
            break;

        // no macros: VIA hides its macro page for a count of 0
        case id_dynamic_keymap_macro_get_count:
            command_data[0] = 0;
            break;

        case id_dynamic_keymap_macro_get_buffer_size:
            command_data[0] = 0;
            command_data[1] = 0;
            break;

        case id_dynamic_keymap_macro_get_buffer:
            for (uint8_t i = 3; i < VIA_REPORT_SIZE - 1u; i++) {
                command_data[i] = 0;
            }
            break;

        case id_dynamic_keymap_macro_set_buffer:
        case id_dynamic_keymap_macro_reset:
            break;

        case id_dynamic_keymap_get_layer_count:
            command_data[0] = DYNAMIC_KEYMAP_LAYER_COUNT;
            break;

        case id_dynamic_keymap_get_buffer: {
            const uint16_t offset = (uint16_t)((uint16_t)command_data[0] << 8 | command_data[1]);
            const uint8_t  size   = command_data[2] < VIA_BUFFER_CHUNK ? command_data[2] : VIA_BUFFER_CHUNK;
            dynamic_keymap_get_buffer(offset, size, &command_data[3]);
            break;
        }

        case id_dynamic_keymap_set_buffer: {
            const uint16_t offset = (uint16_t)((uint16_t)command_data[0] << 8 | command_data[1]);
            const uint8_t  size   = command_data[2] < VIA_BUFFER_CHUNK ? command_data[2] : VIA_BUFFER_CHUNK;
            dynamic_keymap_set_buffer(offset, size, &command_data[3]);
            break;
        }

        default:
            data[0] = id_unhandled;
            break;
    }
}
