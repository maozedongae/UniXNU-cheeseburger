// SPDX-License-Identifier: MIT
// Reboot-into-fastboot control for OnePlus 5 (MSM8998 / cheeseburger).
// Uses MSM8998 IMEM restart_reason@65c (0x146bf65c, value 0x77665500 = fastboot) and PSCI SYSTEM_RESET.
#ifndef BOOT_RESET_CORE_H
#define BOOT_RESET_CORE_H
#include <stdint.h>

enum {
  MSM8998_IMEM_BASE = 0x146bf000u,
  MSM8998_IMEM_SIZE = 0x1000u,
  MSM8998_RESTART_REASON_OFFSET = 0x65cu,
  MSM8998_PON_SOFT_RB_BOOTLOADER = 0x77665500u
};
#endif
