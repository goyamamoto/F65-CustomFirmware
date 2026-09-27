#include "kbdef.h"
#include "gpio.h"
#include "user_matrix.h"
#include "f65_rf.h"

// The columns stay outputs between scans (stock P4CR/P5CR/P6CR), idle high.
// Nothing else is left driving a column low, so user_matrix_scan_pre/post keep
// their empty defaults.

void user_matrix_cols_deselect_all(void)
{
    GPIO_HIGH(4, KB_C_P4_MASK);
    GPIO_HIGH(5, KB_C_P5_MASK);
    GPIO_HIGH(6, KB_C_P6_MASK);
}

void user_matrix_col_select(uint8_t col) // active-low: drive LOW
{
    switch (col) {
        case 0:
            KB_C0 = 0;
            break;
        case 1:
            KB_C1 = 0;
            break;
        case 2:
            KB_C2 = 0;
            break;
        case 3:
            KB_C3 = 0;
            break;
        case 4:
            KB_C4 = 0;
            break;
        case 5:
            KB_C5 = 0;
            break;
        case 6:
            KB_C6 = 0;
            break;
        case 7:
            KB_C7 = 0;
            break;
        case 8:
            KB_C8 = 0;
            break;
        case 9:
            KB_C9 = 0;
            break;
        case 10:
            KB_C10 = 0;
            break;
        case 11:
            KB_C11 = 0;
            break;
        case 12:
            KB_C12 = 0;
            break;
        case 13:
            KB_C13 = 0;
            break;
        case 14:
            KB_C14 = 0;
            break;
        case 15:
            KB_C15 = 0;
            break;
    }
}

void user_matrix_col_deselect(uint8_t col) // active-low: drive HIGH (idle)
{
    switch (col) {
        case 0:
            KB_C0 = 1;
            break;
        case 1:
            KB_C1 = 1;
            break;
        case 2:
            KB_C2 = 1;
            break;
        case 3:
            KB_C3 = 1;
            break;
        case 4:
            KB_C4 = 1;
            break;
        case 5:
            KB_C5 = 1;
            break;
        case 6:
            KB_C6 = 1;
            break;
        case 7:
            KB_C7 = 1;
            break;
        case 8:
            KB_C8 = 1;
            break;
        case 9:
            KB_C9 = 1;
            break;
        case 10:
            KB_C10 = 1;
            break;
        case 11:
            KB_C11 = 1;
            break;
        case 12:
            KB_C12 = 1;
            break;
        case 13:
            KB_C13 = 1;
            break;
        case 14:
            KB_C14 = 1;
            break;
        case 15:
            KB_C15 = 1;
            break;
    }
}

uint8_t user_matrix_read_rows(void)
{
    // R0-R2 = P7.1-P7.3, R3-R4 = P5.3-P5.4 (the stock row byte at 0x6B81 shifted
    // down by one, without its unused P7.0 bit); unused bits read as released.
    const uint8_t rows = (uint8_t)(((P7 >> 1) & 0x07) | (P5 & 0x18) | 0xE0);
    // The scan is the longest stretch with the main loop stopped: look at the
    // radio module's line here, at every column (f65_rf.c).
    rf_rx_frame_isr();
    return rows;
}

void user_matrix_sinks_off(void)
{
    GPIO_LOW(1, LED_P1_MASK);
    GPIO_LOW(2, LED_P2_MASK);
    GPIO_LOW(3, LED_P3_MASK);
}
