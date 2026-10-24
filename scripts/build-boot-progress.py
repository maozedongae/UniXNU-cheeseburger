#!/usr/bin/env python3
"""Build libBootProgress.a for OnePlus 5 (cheeseburger) against the source-kernel ABI."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'kits/cheeseburger'
OUT = ROOT / 'build/boot-progress'
OUT.mkdir(parents=True, exist_ok=True)

record = json.loads(
    (ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json')
    .read_text().strip().rstrip(',')
)
source = KIT / 'BootProgress.c'
obj = OUT / 'BootProgress.o'
command = []
args = record['arguments']
i = 0
while i < len(args):
    arg = args[i]
    if arg == record['file']:
        command.append(str(source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            command += ['-o', str(obj)]
        i += 1
    elif arg == '-Xclang' and args[i + 1].startswith('-W'):
        i += 1
    elif arg == '-D' and args[i + 1].startswith('XNU_BOOT_PROGRESS='):
        i += 1
    elif not arg.startswith(('-W', '-DXNU_BOOT_PROGRESS=')):
        command.append(arg)
    i += 1

command += [
    '-DXNU_BOOT_PROGRESS=1', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
    '-I', str(KIT),
]
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
(OUT / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')

fault_source = KIT / 'BootFaultProbe.c'
fault_obj = OUT / 'BootFaultProbe.o'
fault_command = [
    str(fault_source) if arg == str(source) else str(fault_obj) if arg == str(obj) else arg
    for arg in command
]
subprocess.run(fault_command, cwd=record['directory'], check=True, timeout=90)

policy_source = KIT / 'BootWatchdogPolicy.c'
policy_obj = OUT / 'BootWatchdogPolicy.o'
policy_record = json.loads(
    (ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json')
    .read_text().strip().rstrip(',')
)
policy_command = []
policy_args = policy_record['arguments']
j = 0
while j < len(policy_args):
    arg = policy_args[j]
    if arg == policy_record['file']:
        policy_command.append(str(policy_source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            policy_command += ['-o', str(policy_obj)]
        j += 1
    elif arg == '-Xclang' and policy_args[j + 1].startswith('-W'):
        j += 1
    elif not arg.startswith('-W'):
        policy_command.append(arg)
    j += 1
policy_command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-I', str(KIT)]
subprocess.run(policy_command, cwd=policy_record['directory'], check=True, timeout=90)

diag_source = KIT / 'BootDiagExport.c'
diag_obj = OUT / 'BootDiagExport.o'
diag_command = [
    str(diag_source) if arg == str(policy_source) else str(diag_obj) if arg == str(policy_obj) else arg
    for arg in policy_command
]
subprocess.run(diag_command, cwd=policy_record['directory'], check=True, timeout=90)

archive = OUT / 'libBootProgress.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj), str(fault_obj), str(policy_obj), str(diag_obj)], check=True, timeout=30)
(OUT / 'build-report.json').write_text(json.dumps({
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'board': 'cheeseburger',
}, indent=2) + '\n')
print('PASS: cheeseburger BootProgress archive built')
