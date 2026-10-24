#!/usr/bin/env python3
"""Compile the generic platform expert and CPU driver against the source-kernel ABI."""
import argparse
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('component', choices=[
    'ArmVirtPlatform', 'ArmDTPlatform', 'ArmCpu', 'NativeAES',
    'NativeVirtioBlock', 'NativeRamBlock', 'NativeMobileDisplay',
    'NativeVirtioConsole', 'NativeLegacyDisplay', 'NativeGOPPresenter',
    'NativeUfs', 'NativeHidInput', 'NativeKeyStoreEvents',
])
args, _ = parser.parse_known_args()
component = args.component

recipe = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
record = json.loads(recipe.read_text().strip().rstrip(','))
source = ROOT / 'drivers' / ('NativeMobileDisplay' if component in ('NativeLegacyDisplay', 'NativeGOPPresenter') else component) / (component + '.cpp')
out_map = {
    'ArmVirtPlatform': 'armvirt-platform',
    'ArmDTPlatform': 'armdt-platform',
    'ArmCpu': 'armcpu',
    'NativeAES': 'native-aes',
    'NativeVirtioBlock': 'native-virtio-block',
    'NativeRamBlock': 'native-ram-block',
    'NativeMobileDisplay': 'native-mobile-display',
    'NativeVirtioConsole': 'native-virtio-console',
    'NativeLegacyDisplay': 'native-legacy-display',
    'NativeGOPPresenter': 'native-gop-presenter',
    'NativeUfs': 'native-ufs',
    'NativeHidInput': 'native-hid-input',
    'NativeKeyStoreEvents': 'native-keystore-events',
}
out = ROOT / 'build' / out_map[component]
out.mkdir(parents=True, exist_ok=True)
destination = out / (component + '.o')

arguments = record['arguments']
command = []
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(source))
    elif value == '-o':
        command.extend(['-o', str(destination)])
        index += 1
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1

command.extend([
    '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
    '-Wno-c99-designator', '-Wno-reorder-init-list',
    '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier',
    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
])

(out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
(out / 'compile.log').write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.stdout[-3500:])

data = destination.read_bytes()
magic, cpu, subtype, kind, count, _ = struct.unpack_from('<6I', data)
if (magic, cpu, subtype, kind) != (0xfeedfacf, 0x100000c, 0, 1):
    raise SystemExit('Unexpected platform object architecture')

archive = out / ('lib' + component + '.a')
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(destination)], check=True)
print(f'{component}: -Werror build and ARM64 object audit passed')
