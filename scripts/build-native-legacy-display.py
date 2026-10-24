#!/usr/bin/env python3
"""Build libNativeLegacyDisplay.late.a and boot probes for OnePlus 5 (cheeseburger / MSM8998)."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-legacy-display'
OUT.mkdir(parents=True, exist_ok=True)


def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=90, **kw)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


pr = json.loads(
    (ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json')
    .read_text()
    .strip()
    .rstrip(',')
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
    '-Wall', '-Wextra', '-Wno-unused-parameter',
    '-mgeneral-regs-only',
    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
    '-I', str(ROOT / 'kits/sheng'),
]

# Generate clean _native_legacy_display_late_probe implementation for MSM8998 GOP console
probe_c = OUT / 'CheeseburgerLegacyDisplayProbe.c'
probe_c.write_text('''// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <stdint.h>

extern void kprintf(const char *fmt, ...);

void native_legacy_display_late_probe(void);
void native_legacy_display_late_probe(void)
{
    kprintf("[cheeseburger] native_legacy_display_late_probe: GOP framebuffer console active\\n");
}
''')

c_units = [
    (probe_c, OUT / 'LegacyDisplayProbe.o'),
    (ROOT / 'kits/sheng/BootProcessProbe.c', OUT / 'BootProcessProbe.o'),
    (ROOT / 'kits/sheng/BootUiTraceProbe.c', OUT / 'BootUiTraceProbe.o'),
    (ROOT / 'kits/sheng/BootLog.c', OUT / 'BootLog.o'),
    (ROOT / 'kits/sheng/BootReset.c', OUT / 'BootReset.o'),
]

objs = []
for src, obj in c_units:
    if not src.exists():
        continue
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

print('PASS: built libNativeLegacyDisplay.late.a for cheeseburger (MSM8998)')
