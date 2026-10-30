"""Original SNES LoROM and NES NROM smoke tests: display a blue backdrop.

No external ROM data or toolchain is needed. This fixture tests transfer and
basic launch only; it is not a game compatibility test.
"""
import argparse
import hashlib
import json
import struct
import io
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

SNES_GUID = 'bf3926b2-106d-56c9-8166-0fe29c33bad0'
NES_GUID = 'fad2a671-5d13-54b9-8562-3b058c610348'


def build_demo_rom():
    rom = bytearray([0xff] * 32768)
    # SEI; native mode; 16-bit registers; stack; 8-bit A; forced blank;
    # CGRAM color 0 = blue; full brightness; infinite branch.
    code = bytes.fromhex('78 18 fb c2 38 a2 ff 1f 9a e2 20 a9 80 8d 00 21 9c 21 21 a9 00 8d 22 21 a9 7c 8d 22 21 a9 0f 8d 00 21 80 fe')
    rom[:len(code)] = code
    rom[0x7fc0:0x7fd5] = b'ROMS MANAGER MVP TEST'
    rom[0x7fd5:0x7fdc] = bytes([0x20, 0x00, 0x05, 0x00, 0x01, 0x00, 0x00])
    # All vectors point at startup; interrupts remain disabled.
    for offset in range(0x7fe0, 0x8000, 2):
        struct.pack_into('<H', rom, offset, 0x8000)
    rom[0x7fdc:0x7fe0] = b'\xff\xff\x00\x00'
    checksum = sum(rom) & 0xffff
    struct.pack_into('<HH', rom, 0x7fdc, checksum ^ 0xffff, checksum)
    return bytes(rom)


def build_nes_demo_rom():
    """Original mapper-0 iNES cartridge: initialize the PPU and show blue."""
    prg = bytearray([0xea] * 16384)
    code = bytes.fromhex('78 d8 a2 ff 9a a9 00 8d 00 20 8d 01 20 '
                         '2c 02 20 10 fb 2c 02 20 10 fb '
                         'a9 3f 8d 06 20 a9 00 8d 06 20 a9 21 8d 07 20 '
                         'a9 00 8d 06 20 8d 06 20')
    prg[:len(code)] = code
    loop = 0x8000 + len(code)
    prg[len(code):len(code) + 3] = b'\x4c' + struct.pack('<H', loop)
    struct.pack_into('<HHH', prg, 0x3ffa, loop, 0x8000, loop)
    # 16 KiB NROM PRG, CHR RAM, no trainer or BIOS.
    return b'NES\x1a\x01\x00\x00\x00' + bytes(8) + bytes(prg)


def build_demo_zip(system='nes'):
    payload = build_nes_demo_rom() if system == 'nes' else build_demo_rom()
    extension = 'nes' if system == 'nes' else 'sfc'
    name = 'roms/ROMs Manager %s ZIP Demo.%s' % (system.upper(), extension)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)
    return buffer.getvalue()


def demo_catalog(backend_url, system='snes', archived=False):
    if archived:
        payload = build_nes_demo_rom() if system == 'nes' else build_demo_rom()
        extension = 'nes' if system == 'nes' else 'sfc'
        name = 'ROMs Manager %s ZIP Demo.%s' % (system.upper(), extension)
        return {'guid': NES_GUID if system == 'nes' else SNES_GUID,
                'module': 'Nintendo - Nintendo Entertainment System' if system == 'nes' else 'Nintendo - Super Nintendo Entertainment System',
                'entry_count': 1, 'source_kind': 'homebrew', 'source_label': 'ROMs Manager ZIP smoke test',
                'entries': [{'_key': 'roms-manager-%s-zip-demo' % system, 'name': name.rsplit('.', 1)[0],
                    'http_url': backend_url.rstrip('/') + '/mister/demo.%s.zip' % extension,
                    '_archive_member': True, '_archive_member_path': 'roms/' + name,
                    '_source_bundle_size': len(build_demo_zip(system)), 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}]}
    if system == 'nes':
        rom = build_nes_demo_rom()
        return {'guid': NES_GUID, 'module': 'Nintendo - Nintendo Entertainment System',
                'entry_count': 1, 'source_kind': 'homebrew', 'source_label': 'ROMs Manager smoke test',
                'entries': [{'_key': 'roms-manager-original-nes-demo', 'name': 'ROMs Manager NES Blue Screen Demo',
                    'rom_name': 'ROMs Manager NES Blue Screen Demo.nes', 'size': len(rom),
                    'sha256': hashlib.sha256(rom).hexdigest(), 'http_url': backend_url.rstrip('/') + '/mister/demo.nes'}]}
    if system != 'snes':
        raise ValueError('Unknown demo console')
    rom = build_demo_rom()
    return {'guid': SNES_GUID, 'module': 'Nintendo - Super Nintendo Entertainment System',
            'entry_count': 1, 'source_kind': 'homebrew', 'source_label': 'ROMs Manager smoke test',
            'entries': [{'_key': 'roms-manager-original-demo', 'name': 'ROMs Manager Blue Screen Demo',
                'rom_name': 'ROMs Manager Blue Screen Demo.sfc', 'size': len(rom),
                'sha256': hashlib.sha256(rom).hexdigest(), 'http_url': backend_url.rstrip('/') + '/mister/demo.sfc'}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-url', required=True, help='Address reachable from MiSTer')
    parser.add_argument('--system', choices=('snes', 'nes'), default='snes')
    parser.add_argument('--zip', action='store_true', help='Test backend ZIP-member preparation')
    parser.add_argument('--output')
    args = parser.parse_args()
    parsed = urlsplit(args.backend_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('Use a plain HTTP(S) backend address')
    path = Path(args.output or 'backend/data/roms/mister_%s%s_demo.json' % (args.system, '_zip' if args.zip else ''))
    if path.exists():
        parser.error('Demo catalog already exists; leave it in place or choose another output')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(demo_catalog(args.backend_url, args.system, args.zip), indent=2) + '\n')
    print('Created optional homebrew smoke-test catalog:', path)


if __name__ == '__main__':
    main()
