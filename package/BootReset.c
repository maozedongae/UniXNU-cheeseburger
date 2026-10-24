// SPDX-License-Identifier: MIT
// MSM8998 (OnePlus 5 / cheeseburger) PSCI & IMEM reboot-to-bootloader glue.
#include <kern/thread.h>
#include <machine/machine_routines.h>
#include <string.h>
#include <IOKit/IOLib.h>
#include <pexpert/pexpert.h>
#include "BootResetCore.h"

enum {
  PsciVersion = 0x84000000u,
  PsciFeatures = 0x8400000au,
  PsciSystemReset = 0x84000009u,
  PsciSystemReset2_32 = 0x84000012u,
  PsciSystemReset2_64 = 0xc4000012u
};

volatile int32_t boot_reset_wdt_stats[4];
extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);

static uint64_t psci(uint64_t fid, uint64_t a1) {
  register uint64_t x0 __asm__("x0") = fid, x1 __asm__("x1") = a1, x2 __asm__("x2") = 0, x3 __asm__("x3") = 0;
  __asm__ volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
                   :: "memory", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16", "x17");
  return x0;
}

int boot_reset_probe(char *text, unsigned cap) {
  const uint64_t ver = psci(PsciVersion, 0);
  const int64_t f_reset = (int64_t)psci(PsciFeatures, PsciSystemReset);
  snprintf(text, cap, "msm8998 psci v%x reset=%lld imem=0x%x", (unsigned)ver, (long long)f_reset, MSM8998_IMEM_BASE + MSM8998_RESTART_REASON_OFFSET);
  return 0;
}

int boot_reset_mark_bootloader(char *text, unsigned cap) {
  volatile uint32_t *imem = (volatile uint32_t *)ml_io_map_unmappable(MSM8998_IMEM_BASE, MSM8998_IMEM_SIZE, 7);
  if (!imem) {
    snprintf(text, cap, "msm8998 imem map failed");
    return 1;
  }
  uint32_t before = imem[MSM8998_RESTART_REASON_OFFSET / 4];
  imem[MSM8998_RESTART_REASON_OFFSET / 4] = MSM8998_PON_SOFT_RB_BOOTLOADER;
  __asm__ volatile("dsb sy" ::: "memory");
  uint32_t after = imem[MSM8998_RESTART_REASON_OFFSET / 4];
  ml_io_unmap((vm_offset_t)imem, MSM8998_IMEM_SIZE);
  snprintf(text, cap, "msm8998 imem restart_reason: %08x -> %08x", before, after);
  return 0;
}

int boot_reset_watchdog(uint32_t deadline, char *text, unsigned cap) {
  snprintf(text, cap, "msm8998 watchdog: deadline=%us (no gunyah hypervisor on msm8998)", deadline);
  return 0;
}

int boot_reset_bootloader(char *text, unsigned cap) {
  boot_reset_mark_bootloader(text, cap);
  psci(PsciSystemReset, 0);
  return 99;
}
