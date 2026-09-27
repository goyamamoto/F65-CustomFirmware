// Copyright 2026 goyamamoto
// SPDX-License-Identifier: GPL-2.0-or-later
//
// US-JIS substitution: type a US keyboard as printed on a host that is set
// to the Japanese keyboard layout. See usjis.c for the rules.
#pragma once

#include <stdbool.h>
#include <stdint.h>

// Call first from kb_process_record. Returns false when the event was consumed.
bool usjis_process_record(uint16_t keycode, bool pressed);
// Forget every held key, for example after the host report was cleared.
void usjis_clear(void);
// Request a mode change; applied once no non-modifier key is held.
void usjis_request(bool enable);
void usjis_toggle(void);
// Effective mode (what user_settings stores).
bool usjis_is_enabled(void);
// The OS switch: substitution is only in force on Win.
void usjis_set_win_mode(bool win);
// True while substitution is in force: enabled and the OS switch is on Win.
bool usjis_is_active(void);
