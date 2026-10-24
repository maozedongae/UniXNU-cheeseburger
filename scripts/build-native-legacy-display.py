#!/usr/bin/env python3
"""Build libNativeLegacyDisplay.late.a and boot probes for OnePlus 5 (cheeseburger / MSM8998)."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'kits/cheeseburger'
OUT = ROOT / 'build/native-legacy-display'
OUT.mkdir(parents=True, exist_ok=True)


def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=90, **kw)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


pr = json.loads(
    (ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json')
    .read_text().strip().rstrip(',')
)

base_cmd = []
pa = pr['arguments']
j = 0
while j < len(pa):
    value = pa[j]
    if value == pr['file']:
        base_cmd.append('__SOURCE__')
    elif value in ('-o', '-MF', '-MT', '-MJ'):
        if value == '-o':
            base_cmd += ['-o', '__OBJECT__']
        j += 1
    elif value == '-Xclang' and pa[j + 1].startswith('-W'):
        j += 1
    elif not value.startswith('-W'):
        base_cmd.append(value)
    j += 1

base_cmd += [
    '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
    '-mgeneral-regs-only',
    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
    '-I', str(KIT),
]

probe_c = OUT / 'CheeseburgerLegacyDisplayProbe.c'
probe_c.write_text('''// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <stdint.h>
#include <pexpert/pexpert.h>

extern void kprintf(const char *fmt, ...);
extern void boot_progress_stage(const char *text) __attribute__((weak_import));

volatile uint32_t native_legacy_stage = 0;

void native_legacy_display_probe(void);
void native_legacy_display_probe(void)
{
    native_legacy_stage = 1;
}

void native_legacy_display_late_probe(void);
void native_legacy_display_late_probe(void)
{
    native_legacy_stage = 2;
    if (boot_progress_stage) {
        boot_progress_stage("cheeseburger: GOP framebuffer console ready");
    }
    kprintf("[cheeseburger] native_legacy_display_late_probe: GOP framebuffer active\\n");
}
''')

c_units = [
    (probe_c, OUT / 'LegacyDisplayProbe.o'),
    (KIT / 'BootProcessProbe.c', OUT / 'BootProcessProbe.o'),
    (KIT / 'BootUiTraceProbe.c', OUT / 'BootUiTraceProbe.o'),
    (KIT / 'BootLog.c', OUT / 'BootLog.o'),
    (KIT / 'BootReset.c', OUT / 'BootReset.o'),
]

objs = []
for src, obj in c_units:
    cmd = [
        str(src) if a == '__SOURCE__' else str(obj) if a == '__OBJECT__' else a
        for a in base_cmd
    ]
    run(cmd, cwd=pr['directory'])
    objs.append(obj)

archive = OUT / 'libNativeLegacyDisplay.late.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', str(archive), *[str(o) for o in objs]])

(OUT / 'build-report.json').write_text(json.dumps({
    'archive_sha256': sha(archive),
    'objects': [o.name for o in objs],
    'board': 'cheeseburger',
}, indent=2) + '\n')

print('PASS: cheeseburger libNativeLegacyDisplay.late.a built')
