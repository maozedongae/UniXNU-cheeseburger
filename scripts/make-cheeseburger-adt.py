#!/usr/bin/env python3
"""Generate the hybrid Apple Device Tree (kernel.macho.adt) for OnePlus 5 (cheeseburger / MSM8998).

Takes the authentic iPhone X (iPhone10,3 / D22AP, iOS 16.7.10) binary DeviceTree
from kits/cheeseburger/d22ap.adt (with fallback to a15_tree.adt), preserves 95%
of Apple identity nodes (/product, /defaults, /filesystems, /osenvironments,
/buttons, /backlight, etc.), and surgically transplants Qualcomm Snapdragon 835
(MSM8998) hardware topology (/cpus, /psci, /memory, /arm-io, /chosen).
"""

from pathlib import Path
import argparse
import os
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Qualcomm Snapdragon 835 (MSM8998) / OnePlus 5 (cheeseburger) Hardware Map
# ---------------------------------------------------------------------------
DRAM_BASE = 0x80000000
DEFAULT_DRAM_SIZE = 0x180000000        # 6 GiB LPDDR4X on OnePlus 5
DEFAULT_OWNED_MEM = 0x10000000         # 256 MiB contiguous DarwinBoot window
TIMEBASE_FREQ = 19200000               # 19.2 MHz Qualcomm XO architectural timer

# APSS peripheral window for ArmDTPlatform (Variant A, matching UniXNU convention)
ARMIO_BASE = 0x17000000
ARMIO_SIZE = 0x08000000

# GICv3 (interrupt-controller@17a00000 in msm8998.dtsi)
GIC_DIST_PHYS = 0x17A00000
GIC_DIST_SIZE = 0x10000
GIC_REDIST_PHYS = 0x17B00000
GIC_REDIST_SIZE = 0x100000             # 8 cores * 0x20000 (128 KiB per redistributor)
GIC_REDIST_STRIDE = 0x20000            # MSM8998 GICv3 stride (msm8998.dtsi line 318)
GIC_ITS_PHYS = 0x17A20000
GIC_ITS_SIZE = 0x20000

# APSS peripherals inside the 0x17000000..0x1f000000 arm-io window
APSS_WDT_PHYS = 0x17817000
APSS_WDT_SIZE = 0x1000
APSS_TIMER_PHYS = 0x17920000
APSS_TIMER_SIZE = 0x1000
APSS_OSM_CLK_PHYS = 0x179C0000
APSS_OSM_CLK_SIZE = 0x4000
APSS_PWRCL_PLL_PHYS = 0x17916000
APSS_PERFCL_PLL_PHYS = 0x17816000

# SoC peripherals below 0x17000000 (matched by class; physical bases in properties/constants)
GCC_PHYS = 0x00100000
GCC_SIZE = 0x000B0000
PSHOLD_PHYS = 0x010AC000
PSHOLD_SIZE = 0x4
UFS_HC_PHYS = 0x01DA4000
UFS_HC_SIZE = 0x2500
UFS_PHY_PHYS = 0x01DA7000
UFS_PHY_SIZE = 0xDA8
UFS_ICE_PHYS = 0x01DB0000
UFS_ICE_SIZE = 0x8000
TLMM_PHYS = 0x03400000
TLMM_SIZE = 0xC00000
GPUCC_PHYS = 0x05065000
GPUCC_SIZE = 0x9000
SPMI_PHYS = 0x0800F000
SPMI_SIZE = 0x1000
USB3_DWC3_PHYS = 0x0A800000
USB3_DWC3_SIZE = 0xF8C00
USB3_SSPHY_PHYS = 0x0C010000
USB3_SSPHY_SIZE = 0xE0C
USB3_QUSB2PHY_PHYS = 0x0C012000
USB3_QUSB2PHY_SIZE = 0x2A8
UART_BLSP2_DM1_PHYS = 0x0C1B0000
UART_BLSP2_DM1_SIZE = 0x1000
MMSSCC_PHYS = 0x0C8C0000
MMSSCC_SIZE = 0x40000

# Kryo 280 8-core MPIDR topology (4x Silver Cortex-A53 + 4x Gold Cortex-A73)
KRYO280_CORES = [
    # (index, mpidr_reg, cluster_id, cluster_type, compat, max_freq_hz, l2_size, l2_id)
    (0, 0x000, 0, b'E\0', b'arm,cortex-a53\0arm,armv8\0ARM,v8\0', 1881600000, 0x100000, 0),
    (1, 0x001, 0, b'E\0', b'arm,cortex-a53\0arm,armv8\0ARM,v8\0', 1881600000, 0x100000, 0),
    (2, 0x002, 0, b'E\0', b'arm,cortex-a53\0arm,armv8\0ARM,v8\0', 1881600000, 0x100000, 0),
    (3, 0x003, 0, b'E\0', b'arm,cortex-a53\0arm,armv8\0ARM,v8\0', 1881600000, 0x100000, 0),
    (4, 0x100, 1, b'P\0', b'arm,cortex-a73\0arm,armv8\0ARM,v8\0', 2457600000, 0x200000, 1),
    (5, 0x101, 1, b'P\0', b'arm,cortex-a73\0arm,armv8\0ARM,v8\0', 2457600000, 0x200000, 1),
    (6, 0x102, 1, b'P\0', b'arm,cortex-a73\0arm,armv8\0ARM,v8\0', 2457600000, 0x200000, 1),
    (7, 0x103, 1, b'P\0', b'arm,cortex-a73\0arm,armv8\0ARM,v8\0', 2457600000, 0x200000, 1),
]

PROP_NAME_LEN = 32
DT_PROP_LEN_MASK = 0x0FFFFFFF


# ---------------------------------------------------------------------------
# Apple DeviceTree In-Memory Representation & Binary Parser / Serializer
# ---------------------------------------------------------------------------
class DTNode:
    """Mutable representation of an Apple DeviceTree node (DT_NODE)."""

    def __init__(self, name: str, properties=None, children=None):
        self.name = name
        self.properties = []  # list of [key: str, data: bytes]
        self.children = []    # list of DTNode
        if properties:
            for k, v in properties:
                self.set_prop(k, v)
        if children:
            self.children.extend(children)

    def get_prop(self, key: str):
        for k, v in self.properties:
            if k == key:
                return v
        return None

    def set_prop(self, key: str, data: bytes):
        if key == 'name':
            self.name = data.rstrip(b'\x00').decode('utf-8', errors='replace')
            return
        for item in self.properties:
            if item[0] == key:
                item[1] = bytes(data)
                return
        self.properties.append([key, bytes(data)])

    def del_prop(self, key: str):
        self.properties = [item for item in self.properties if item[0] != key]

    def get_child(self, name: str):
        for child in self.children:
            if child.name == name:
                return child
        return None

    def remove_child(self, name: str):
        self.children = [c for c in self.children if c.name != name]

    def replace_or_add_child(self, new_child: 'DTNode', after: str = None):
        for idx, child in enumerate(self.children):
            if child.name == new_child.name:
                self.children[idx] = new_child
                return
        if after is not None:
            for idx, child in enumerate(self.children):
                if child.name == after:
                    self.children.insert(idx + 1, new_child)
                    return
        self.children.append(new_child)

    def serialize(self) -> bytes:
        """Serialize node and children into Apple DeviceTree binary format."""
        name_bytes = self.name.encode('utf-8') + b'\x00'
        all_props = [('name', name_bytes)] + [
            (k, v) for k, v in self.properties if k != 'name'
        ]
        out = bytearray(struct.pack('<II', len(all_props), len(self.children)))
        for key, data in all_props:
            key_encoded = key.encode('utf-8')[:PROP_NAME_LEN]
            out += struct.pack('<32sI', key_encoded, len(data))
            out += data
            pad = (-len(data)) % 4
            if pad:
                out += b'\x00' * pad
        for child in self.children:
            out += child.serialize()
        return bytes(out)


def _find_adt_root_offset(blob: bytes) -> int:
    """Locate the start of the root 'device-tree' node (handles raw ADT and IM4P)."""
    if len(blob) >= 44:
        nprops, nchildren = struct.unpack_from('<II', blob, 0)
        if 1 <= nprops <= 256 and 1 <= nchildren <= 256:
            return 0

    needle = b'name' + (b'\x00' * 28) + struct.pack('<I', 12) + b'device-tree\x00'
    pos = blob.find(needle)
    if pos >= 8:
        nprops, nchildren = struct.unpack_from('<II', blob, pos - 8)
        if 1 <= nprops <= 256 and 1 <= nchildren <= 256:
            return pos - 8

    raise ValueError('Could not locate valid Apple DeviceTree root header in donor file')


def _parse_node_at(blob: bytes, offset: int):
    """Recursively deserialize a DT_NODE from binary Apple DeviceTree bytes."""
    if offset + 8 > len(blob):
        raise ValueError(f'Truncated ADT node header at offset {offset:#x}')

    nprops, nchildren = struct.unpack_from('<II', blob, offset)
    offset += 8

    node_name = ''
    props = []
    for _ in range(nprops):
        if offset + 36 > len(blob):
            raise ValueError(f'Truncated ADT property header at offset {offset:#x}')
        raw_name, raw_len = struct.unpack_from('<32sI', blob, offset)
        offset += 36
        prop_name = raw_name.split(b'\x00', 1)[0].decode('utf-8', errors='replace')
        prop_len = raw_len & DT_PROP_LEN_MASK
        if offset + prop_len > len(blob):
            raise ValueError(
                f'Property {prop_name!r} length {prop_len:#x} exceeds buffer at {offset:#x}'
            )
        prop_data = blob[offset:offset + prop_len]
        offset += (prop_len + 3) & ~3

        if prop_name == 'name':
            node_name = prop_data.rstrip(b'\x00').decode('utf-8', errors='replace')
        else:
            props.append([prop_name, prop_data])

    children = []
    for _ in range(nchildren):
        child, offset = _parse_node_at(blob, offset)
        children.append(child)

    return DTNode(node_name, props, children), offset


def load_donor_adt(explicit_path: Path = None) -> tuple[DTNode, Path]:
    """Find and deserialize the donor Apple DeviceTree binary."""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates.extend([
        ROOT / 'kits/cheeseburger/d22ap.adt',
        ROOT / 'kits/cheeseburger/a15_tree.adt',
        ROOT / 'd22ap.adt',
        ROOT / 'a15_tree.adt',
        Path(__file__).resolve().parent / 'd22ap.adt',
        Path(__file__).resolve().parent / 'a15_tree.adt',
    ])

    donor_path = next((p for p in candidates if p.is_file()), None)
    if donor_path is None:
        searched = ', '.join(str(p) for p in candidates)
        raise FileNotFoundError(
            f'Donor ADT binary not found. Searched: {searched}'
        )

    raw = donor_path.read_bytes()
    root_off = _find_adt_root_offset(raw)
    root_node, _ = _parse_node_at(raw, root_off)
    return root_node, donor_path


# ---------------------------------------------------------------------------
# CHRP NVRAM Proxy Builder (Required by ArmDTPlatform / IODTNVRAM)
# ---------------------------------------------------------------------------
def build_empty_nvram_proxy() -> list[tuple[str, bytes]]:
    """Build a valid 8192-byte volatile CHRP NVRAM bank with Adler-32 checksum."""
    def chrp_header(name: bytes, size: int) -> bytearray:
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = chrp_header(b'nvram', 32)
    image[32:48] = chrp_header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = chrp_header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [
        ('nvram-bank-size', struct.pack('<I', len(image))),
        ('nvram-bank-count', struct.pack('<I', 1)),
        ('nvram-current-bank', struct.pack('<I', 0)),
        ('nvram-proxy-data', bytes(image)),
    ]


# ---------------------------------------------------------------------------
# Hardware Node Builders for MSM8998 (OnePlus 5 / cheeseburger)
# ---------------------------------------------------------------------------
def build_msm8998_cpus(phandle_base: int = 16, intc_phandle: int = 28) -> DTNode:
    """Construct /cpus with all 8 Kryo 280 cores (4x Silver + 4x Gold)."""
    cpu_children = []
    for idx, mpidr, cluster_id, cluster_type, compat, max_freq, l2_sz, l2_id in KRYO280_CORES:
        state = b'running\0' if idx == 0 else b'waiting\0'
        cpu_node = DTNode(f'cpu{idx}', [
            ('AAPL,phandle', struct.pack('<I', phandle_base + 1 + idx)),
            ('reg', struct.pack('<I', mpidr)),
            ('cpu-id', struct.pack('<I', idx)),
            ('cluster-id', struct.pack('<I', cluster_id)),
            ('cluster-core-id', struct.pack('<I', idx % 4)),
            ('die-cluster-id', struct.pack('<I', cluster_id)),
            ('cluster-type', cluster_type),
            ('device_type', b'cpu\0'),
            ('compatible', compat),
            ('state', state),
            ('enable-method', b'psci\0'),
            ('timebase-frequency', struct.pack('<I', TIMEBASE_FREQ)),
            ('fixed-frequency', struct.pack('<I', TIMEBASE_FREQ)),
            ('clock-frequency', struct.pack('<Q', max_freq)),
            ('l2-cache-size', struct.pack('<I', l2_sz)),
            ('l2-cache-id', struct.pack('<I', l2_id)),
            ('interrupt-parent', struct.pack('<I', intc_phandle)),
        ])
        cpu_children.append(cpu_node)

    return DTNode('cpus', [
        ('AAPL,phandle', struct.pack('<I', phandle_base)),
        ('#address-cells', struct.pack('<I', 1)),
        ('#size-cells', struct.pack('<I', 0)),
    ], cpu_children)


def build_psci_node(method: str = 'smc') -> DTNode:
    """Construct root /psci node for SMP secondary core bring-up."""
    return DTNode('psci', [
        ('compatible', b'arm,psci-1.0\0'),
        ('method', method.encode('utf-8') + b'\0'),
        ('device_type', b'psci\0'),
    ])


def build_msm8998_arm_io(armio_phandle: int = 27, intc_phandle: int = 28) -> DTNode:
    """Construct clean /arm-io for MSM8998 using Variant A (0x17000000 APSS window)."""
    qusb2_init_seq = struct.pack(
        '<16I',
        0x80, 0x000,
        0x13, 0x004,
        0x7C, 0x18C,
        0x80, 0x02C,
        0x0A, 0x184,
        0x8C, 0x21C,
        0xF0, 0x23C,
        0x0F, 0x240,
    )

    children = [
        # 1. ARM GICv3 Interrupt Controller (offsets relative to ARMIO_BASE 0x17000000)
        DTNode('intc', [
            ('AAPL,phandle', struct.pack('<I', intc_phandle)),
            ('compatible', b'arm,gic-v3\0'),
            ('device_type', b'interrupt-controller\0'),
            ('interrupt-controller', b''),
            ('#interrupt-cells', struct.pack('<I', 3)),
            ('#address-cells', struct.pack('<I', 0)),
            ('#size-cells', struct.pack('<I', 0)),
            ('#redistributor-regions', struct.pack('<I', 1)),
            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE)),
            ('reg', struct.pack(
                '<4Q',
                GIC_DIST_PHYS - ARMIO_BASE, GIC_DIST_SIZE,
                GIC_REDIST_PHYS - ARMIO_BASE, GIC_REDIST_SIZE,
            )),
        ]),

        # 2. APSS Architectural & Memory-Mapped Timer (0x17920000)
        DTNode('timer', [
            ('compatible', b'arm,armv8-timer\0arm,armv7-timer-mem\0'),
            ('device_type', b'timer\0'),
            ('clock-frequency', struct.pack('<I', TIMEBASE_FREQ)),
            ('reg', struct.pack('<2Q', APSS_TIMER_PHYS - ARMIO_BASE, APSS_TIMER_SIZE)),
        ]),

        # 3. APSS Watchdog (0x17817000)
        DTNode('wdt', [
            ('compatible', b'qcom,msm-watchdog\0'),
            ('device_type', b'wdt\0'),
            ('reg', struct.pack('<2Q', APSS_WDT_PHYS - ARMIO_BASE, APSS_WDT_SIZE)),
            ('qcom,bark-time', struct.pack('<I', 11000)),
            ('qcom,pet-time', struct.pack('<I', 10000)),
        ]),

        # 4. APSS OSM CPU Clock Controller (0x179c0000)
        DTNode('clock-cpu', [
            ('compatible', b'qcom,cpu-clock-osm-msm8998-v1\0'),
            ('device_type', b'clock-controller\0'),
            ('reg', struct.pack(
                '<6Q',
                APSS_OSM_CLK_PHYS - ARMIO_BASE, APSS_OSM_CLK_SIZE,
                APSS_PWRCL_PLL_PHYS - ARMIO_BASE, 0x1000,
                APSS_PERFCL_PLL_PHYS - ARMIO_BASE, 0x1000,
            )),
            ('qcom,xo-clk-rate', struct.pack('<I', TIMEBASE_FREQ)),
        ]),

        # 5. UFS 2.1 Host Controller + QMP v3 PHY + ICE (0x1da4000; below 0x17000000 window)
        DTNode('ufshc', [
            ('compatible', b'qcom,msm8998-ufshc\0qcom,ufshc\0'),
            ('device_type', b'ufshc\0'),
            ('lanes-per-direction', struct.pack('<I', 1)),
            ('interrupt-parent', struct.pack('<I', intc_phandle)),
            ('interrupts', struct.pack('<3I', 0, 265, 4)),
            ('qcom,ufshc-phys-reg', struct.pack('<2Q', UFS_HC_PHYS, UFS_HC_SIZE)),
            ('qcom,ufsphy-phys-reg', struct.pack('<2Q', UFS_PHY_PHYS, UFS_PHY_SIZE)),
            ('qcom,ufsice-phys-reg', struct.pack('<2Q', UFS_ICE_PHYS, UFS_ICE_SIZE)),
        ]),

        # 6. Debug Serial UART BLSP2 DM1 (0x0c1b0000)
        DTNode('uart0', [
            ('compatible', b'qcom,msm-uartdm-v1.4\0qcom,msm-uartdm\0'),
            ('device_type', b'uart\0'),
            ('interrupt-parent', struct.pack('<I', intc_phandle)),
            ('interrupts', struct.pack('<3I', 0, 114, 4)),
            ('qcom,uart-phys-reg', struct.pack('<2Q', UART_BLSP2_DM1_PHYS, UART_BLSP2_DM1_SIZE)),
        ]),

        # 7. Synopsys DWC3 USB 3.0 + OnePlus 5 QUSB2 PHY + QMP SSPHY (0x0a800000)
        DTNode('usb3', [
            ('compatible', b'qcom,dwc-usb3-msm\0snps,dwc3\0'),
            ('device_type', b'usb\0'),
            ('interrupt-parent', struct.pack('<I', intc_phandle)),
            ('interrupts', struct.pack('<3I', 0, 131, 4)),
            ('qcom,dwc3-phys-reg', struct.pack('<2Q', USB3_DWC3_PHYS, USB3_DWC3_SIZE)),
            ('qcom,qusb2phy-phys-reg', struct.pack('<2Q', USB3_QUSB2PHY_PHYS, USB3_QUSB2PHY_SIZE)),
            ('qcom,ssphy-phys-reg', struct.pack('<2Q', USB3_SSPHY_PHYS, USB3_SSPHY_SIZE)),
            ('qcom,qusb-phy-init-seq', qusb2_init_seq),
        ]),

        # 8. Global / Multimedia / GPU Clock Controllers (GCC 0x100000, MMSS 0xc8c0000, GPU 0x5065000)
        DTNode('gcc', [
            ('compatible', b'qcom,gcc-8998\0'),
            ('device_type', b'clock-controller\0'),
            ('qcom,gcc-phys-reg', struct.pack('<2Q', GCC_PHYS, GCC_SIZE)),
            ('qcom,mmsscc-phys-reg', struct.pack('<2Q', MMSSCC_PHYS, MMSSCC_SIZE)),
            ('qcom,gpucc-phys-reg', struct.pack('<2Q', GPUCC_PHYS, GPUCC_SIZE)),
        ]),

        # 9. TLMM GPIO / Pin Controller (0x03400000)
        DTNode('tlmm', [
            ('compatible', b'qcom,msm8998-pinctrl\0'),
            ('device_type', b'gpio\0'),
            ('qcom,tlmm-phys-reg', struct.pack('<2Q', TLMM_PHYS, TLMM_SIZE)),
        ]),

        # 10. SPMI PMIC Arbiter (PM8998 / PMI8998 @ 0x0800f000)
        DTNode('spmi', [
            ('compatible', b'qcom,spmi-pmic-arb\0'),
            ('device_type', b'spmi\0'),
            ('qcom,spmi-phys-reg', struct.pack(
                '<10Q',
                0x0800F000, 0x00001000,
                0x08400000, 0x01000000,
                0x09400000, 0x01000000,
                0x0A400000, 0x00220000,
                0x0800A000, 0x00003000,
            )),
        ]),

        # 11. Qualcomm PSHOLD Restart Controller (0x010ac000)
        DTNode('pshold', [
            ('compatible', b'qcom,pshold\0'),
            ('device_type', b'restart\0'),
            ('qcom,pshold-phys-reg', struct.pack('<4Q', PSHOLD_PHYS, PSHOLD_SIZE, 0x01FD3000, 0x4)),
        ]),

        # 12. OnePlus 5 Synaptics S3320 Touchscreen (I2C5 @ 0x20, 1080x1920)
        DTNode('synaptics-ts', [
            ('compatible', b'HWK,synaptics,s3320\0synaptics,rmi4\0'),
            ('device_type', b'multi-touch\0'),
            ('i2c-address', struct.pack('<I', 0x20)),
            ('display-coords', struct.pack('<2I', 1080, 1920)),
            ('irq-gpio', struct.pack('<I', 125)),
            ('reset-gpio', struct.pack('<I', 89)),
        ]),

        # 13. OnePlus 5 Alert Slider (tri_state_key on TLMM GPIOs 40, 42, 9)
        DTNode('tri-state-key', [
            ('compatible', b'oneplus,tri-state-key\0'),
            ('device_type', b'tri-state-key\0'),
            ('tristate-gpios', struct.pack('<3I', 40, 42, 9)),
        ]),

        # 14. OnePlus 5 FPC1020 Fingerprint / Capacitive Home Sensor
        DTNode('fpc1020', [
            ('compatible', b'fpc,fpc1020\0'),
            ('irq-gpio', struct.pack('<I', 121)),
            ('id-gpios', struct.pack('<3I', 39, 41, 63)),
        ]),
    ]

    return DTNode('arm-io', [
        ('AAPL,phandle', struct.pack('<I', armio_phandle)),
        ('device_type', b'arm-io\0'),
        ('compatible', b'arm-io,msm8998\0qcom,msm8998\0simple-bus\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
        ('ranges', struct.pack('<QQQ', 0, ARMIO_BASE, ARMIO_SIZE)),
        ('clock-frequencies', bytes(4)),
    ], children)


# ---------------------------------------------------------------------------
# D22AP (iPhone10,3) Identity Enforcement
# ---------------------------------------------------------------------------
def enforce_d22ap_identity(root: DTNode):
    """Ensure root and /product carry iPhone10,3 (D22AP) identity for iOS 16.7.10 arm64."""
    root.set_prop('model', b'iPhone10,3\0')
    root.set_prop('target-type', b'D22\0')
    root.set_prop('target-sub-type', b'D22AP\0')
    root.set_prop(
        'compatible',
        b'D22AP\0iPhone10,3\0AppleARM\0oneplus,cheeseburger\0qcom,msm8998\0',
    )
    root.set_prop('#address-cells', struct.pack('<I', 2))
    root.set_prop('#size-cells', struct.pack('<I', 2))
    root.set_prop('clock-frequency', struct.pack('<I', TIMEBASE_FREQ))

    product = root.get_child('product')
    if product is not None:
        product.set_prop('product-name', b'iPhone X\0')
        product.set_prop('product-description', b'iPhone X\0')
        product.set_prop('artwork-device-idiom', b'phone\0')
        product.set_prop('artwork-device-subtype', struct.pack('<I', 0x984))  # 2436
        product.set_prop('artwork-scale-factor', struct.pack('<I', 3))
        product.set_prop('artwork-display-gamut', b'P3\0')
        product.set_prop('compatible-device-fallback', b'iPhone9,2\0')
        product.set_prop('graphics-featureset-class', b'APPLE4\0')
        product.set_prop(
            'graphics-featureset-fallbacks',
            b'APPLE3:APPLE3v1:APPLE2:APPLE1:GLES2,0\0',
        )
        product.set_prop('device-perf-memory-class', struct.pack('<I', 4))
        product.set_prop('thin-bezel', struct.pack('<I', 1))
        product.set_prop('oled-display', struct.pack('<I', 1))
        product.set_prop('wifi-chipset', b'4357\0')
        product.set_prop('baseband-chipset', b'mav17\0')
        product.set_prop('udid-version', struct.pack('<I', 1))


# ---------------------------------------------------------------------------
# Argument Parsing & Main Pipeline
# ---------------------------------------------------------------------------
def parse_int_arg(val: str, default: int = None, base: int = 0) -> int:
    if val is None or val.lower() in ('required', 'auto', 'default', ''):
        return default
    try:
        return int(val, base)
    except ValueError:
        return int(val, 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-o', '--output', type=Path,
        default=ROOT / 'build/cheeseburger/boot/kernel.macho.adt',
        help='Output binary Apple DeviceTree path',
    )
    parser.add_argument(
        '--base-adt', type=Path, default=None,
        help='Optional explicit path to donor d22ap.adt / a15_tree.adt',
    )
    parser.add_argument(
        '--dram-size', type=lambda v: parse_int_arg(v, DEFAULT_DRAM_SIZE, 0),
        default=DEFAULT_DRAM_SIZE,
        help='Observed DRAM size in bytes (default: 0x180000000 for 6 GiB)',
    )
    parser.add_argument(
        '--owned-memory-size', type=lambda v: parse_int_arg(v, DEFAULT_OWNED_MEM, 0),
        default=DEFAULT_OWNED_MEM,
        help='DarwinBoot contiguous window request (default: 0x10000000)',
    )
    parser.add_argument(
        '--chip-id', type=lambda v: parse_int_arg(v, 0x8998, 16),
        default=0x8998,
        help='/chosen/chip-id (4 bytes, default: 0x8998)',
    )
    parser.add_argument(
        '--unique-chip-id', type=lambda v: parse_int_arg(v, 0x000089981030D22A, 16),
        default=0x000089981030D22A,
        help='/chosen/unique-chip-id (8 bytes seed for IOPlatformUUID)',
    )
    parser.add_argument(
        '--cpu-regs', default='0x0,0x1,0x2,0x3,0x100,0x101,0x102,0x103',
        help='CPU MPIDR affinity values (always emits all 8 Kryo 280 cores on cheeseburger)',
    )
    parser.add_argument(
        '--psci-method', choices=['smc', 'hvc'], default='smc',
        help='/psci conduit method (msm8998.dtsi uses smc)',
    )
    parser.add_argument(
        '--root-match-volume-uuid', default=None,
        help='/chosen/root-matching IOMedia UUID',
    )
    parser.add_argument('--ipad-identity', action='store_true', help='Unused compatibility flag')
    parser.add_argument('--data-ramdisk', action='store_true', help='Enable scratch Data RAMDisk')
    parser.add_argument('--packed-data-ramdisk', action='store_true', help='Enable DBRDISK1 PackedRAMDisk')
    parser.add_argument('--persistent-data', action='store_true', help='Persistent Data volume across boots')
    parser.add_argument('--trust-cache', action='store_true', help='Enable TrustCache placeholder')
    parser.add_argument('--owner-trust-cache', action='store_true', help='Enable owner TrustCache placeholder')
    parser.add_argument('--gpu-addon', action='store_true', help='Enable GpuAddon placeholder')
    parser.add_argument(
        '--text-fixup', action='append', default=[],
        help='/chosen/text-fixups entry <file>,<offset>,<orig>,<repl>',
    )

    args, _ = parser.parse_known_args()

    # 1. Load authentic Apple donor tree (kits/cheeseburger/d22ap.adt)
    root, donor_path = load_donor_adt(args.base_adt)

    # 2. Extract original phandles from donor before surgical replacement
    armio_orig = root.get_child('arm-io')
    armio_phandle = 27
    intc_phandle = 28
    if armio_orig is not None:
        raw_ph = armio_orig.get_prop('AAPL,phandle')
        if raw_ph and len(raw_ph) == 4:
            armio_phandle = struct.unpack('<I', raw_ph)[0]
        aic_orig = armio_orig.get_child('aic')
        if aic_orig is not None:
            raw_aic_ph = aic_orig.get_prop('AAPL,phandle')
            if raw_aic_ph and len(raw_aic_ph) == 4:
                intc_phandle = struct.unpack('<I', raw_aic_ph)[0]

    cpus_orig = root.get_child('cpus')
    cpus_phandle = 16
    if cpus_orig is not None:
        raw_cph = cpus_orig.get_prop('AAPL,phandle')
        if raw_cph and len(raw_cph) == 4:
            cpus_phandle = struct.unpack('<I', raw_cph)[0]

    # 3. Enforce iPhone10,3 (D22AP) identity in root and /product
    enforce_d22ap_identity(root)
    root.remove_child('pram')
    root.remove_child('vram')
    root.remove_child('baseband')

    # 4. Patch /chosen and /chosen/memory-map for DarwinBoot & Hactivation
    chosen = root.get_child('chosen')
    if chosen is None:
        chosen = DTNode('chosen')
        root.children.insert(0, chosen)

    chosen.remove_child('lock-regs')

    chosen.set_prop('random-seed', os.urandom(64))
    chosen.set_prop('dram-base', struct.pack('<Q', DRAM_BASE))
    chosen.set_prop('dram-size', struct.pack('<Q', args.dram_size))
    if args.owned_memory_size:
        chosen.set_prop('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))

    chosen.set_prop('chip-id', struct.pack('<I', args.chip_id))
    chosen.set_prop('unique-chip-id', struct.pack('<Q', args.unique_chip_id))
    chosen.set_prop('board-id', struct.pack('<I', 6))
    chosen.set_prop('osenvironment', b'main\0')
    chosen.set_prop('crypto-hash-method', b'sha2-384\0')
    chosen.set_prop('boot-manifest-hash', bytes(48))

    chosen.set_prop('certificate-production-status', struct.pack('<I', 0))
    chosen.set_prop('effective-production-status-ap', struct.pack('<I', 0))
    chosen.set_prop('effective-production-status-sep', struct.pack('<I', 0))
    chosen.set_prop('certificate-security-mode', struct.pack('<I', 1))
    chosen.set_prop('effective-security-mode-ap', struct.pack('<I', 1))
    chosen.set_prop('effective-security-mode-sep', struct.pack('<I', 1))
    chosen.set_prop('amfi-only-platform-code', struct.pack('<I', 1))
    chosen.set_prop('amfi-allows-trust-cache-load', struct.pack('<I', 1))
    chosen.set_prop('sepfw-load-at-boot', struct.pack('<I', 0))
    chosen.set_prop('system-trusted', struct.pack('<I', 1))

    if (args.data_ramdisk or args.packed_data_ramdisk) and not args.persistent_data:
        chosen.set_prop('ephemeral-storage', struct.pack('<I', 1))
    else:
        chosen.del_prop('ephemeral-storage')

    if args.root_match_volume_uuid:
        uuid_xml = (
            '<dict><key>IOProviderClass</key><string>IOMedia</string>'
            '<key>IOPropertyMatch</key><dict><key>UUID</key><string>'
            f'{args.root_match_volume_uuid.upper()}'
            '</string></dict></dict>\0'
        ).encode('utf-8')
        chosen.set_prop('root-matching', uuid_xml)
    else:
        chosen.del_prop('root-matching')

    if args.text_fixup:
        chosen.set_prop('text-fixups', b''.join(f.encode('utf-8') + b'\0' for f in args.text_fixup))

    for nv_key, nv_val in build_empty_nvram_proxy():
        chosen.set_prop(nv_key, nv_val)

    mem_map = chosen.get_child('memory-map')
    if mem_map is None:
        mem_map = DTNode('memory-map', [('AAPL,phandle', struct.pack('<I', 7))])
        chosen.children.insert(0, mem_map)

    if args.gpu_addon:
        mem_map.set_prop('GpuAddon', bytes(16))

    if chosen.get_child('manifest-properties') is None:
        chosen.children.append(DTNode('manifest-properties'))

    # 5. Replace /cpus with 8x Kryo 280 cores and add /psci
    new_cpus = build_msm8998_cpus(phandle_base=cpus_phandle, intc_phandle=intc_phandle)
    root.replace_or_add_child(new_cpus)
    root.replace_or_add_child(build_psci_node(args.psci_method), after='cpus')

    # 6. Patch /memory with OnePlus 5 DRAM range (0x80000000, 0x180000000)
    memory_node = root.get_child('memory')
    if memory_node is None:
        memory_node = DTNode('memory', [
            ('AAPL,phandle', struct.pack('<I', 24)),
            ('device_type', b'memory\0'),
        ])
        root.replace_or_add_child(memory_node, after='aliases')
    memory_node.set_prop('device_type', b'memory\0')
    memory_node.set_prop('reg', struct.pack('<QQ', DRAM_BASE, args.dram_size))

    # 7. Replace /arm-io with clean MSM8998 nodes (Variant A: 0x17000000 base)
    new_armio = build_msm8998_arm_io(armio_phandle=armio_phandle, intc_phandle=intc_phandle)
    root.replace_or_add_child(new_armio)

    # 8. Sanitize /defaults for MSM8998
    defaults_node = root.get_child('defaults')
    if defaults_node is None:
        defaults_node = DTNode('defaults')
        root.children.append(defaults_node)
    defaults_node.del_prop('pmap-io-ranges')
    defaults_node.set_prop('sleep-disabled', struct.pack('<I', 1))
    defaults_node.set_prop('l2-ecc-correctable-panic', struct.pack('<I', 0))
    defaults_node.set_prop('sep-is-firmware-payload', struct.pack('<I', 0))
    defaults_node.set_prop('kern.maxvnodes', struct.pack('<I', 66560))

    # 9. Serialize final binary Apple DeviceTree
    adt_bytes = root.serialize()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(adt_bytes)

    print(
        f'cheeseburger ADT: {len(adt_bytes)} bytes (donor: {donor_path.name}), '
        f'dram {DRAM_BASE:#x}+{args.dram_size:#x}, '
        f'8x Kryo 280, GICv3 phandle={intc_phandle}, timebase {TIMEBASE_FREQ} Hz'
    )


if __name__ == '__main__':
    sys.exit(main())
