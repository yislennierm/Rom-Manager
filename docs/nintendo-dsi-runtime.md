# Nintendo DSi Runtime Notes

Nintendo DSi is not a normal cartridge-console install path in this app. Most
systems can copy the downloaded ROM into the RetroArch downloads folder, write a
playlist entry, and let a core boot the file directly. DSiWare needs extra
runtime preparation before that works reliably.

## Why DSi Is Different

- DSiWare provider files are commonly title-named ZIP files containing payloads
  with generic names such as `00000000`.
- RetroArch playlists need a readable, stable content filename, so the installer
  extracts the payload and installs it as a title-named `.dsi` file.
- melonDS DS DSi mode requires DS BIOS, DSi BIOS, firmware, and NAND files. DS
  cartridge mode can fall back to FreeBIOS; DSiWare cannot.
- DSiWare has region behavior. melonDS DS can auto-select a matching regional
  NAND when `melonds_dsi_nand_path = "/auto"` and regional NAND files are
  available.
- melonDS DS may need TMD metadata for DSiWare. The app does not manage TMD
  files directly; the core can cache/download them during launch.
- The DSi SD card core option must stay disabled by default, because enabling it
  can create a multi-gigabyte SD image.

## Current App Behavior

- `data/emulators/cores.json` declares the `melonds_dsiware` install strategy for
  Nintendo DSi only.
- The installer extracts DSiWare archives, selects the runtime payload, and
  writes a named `.dsi` file into the active RetroArch downloads tree.
- Playlists are limited to DSi-compatible extensions: `.dsi`, `.nds`, and `.ids`.
- The installer mirrors required melonDS DS system files into
  `system/melonDS DS`.
- The installer writes melonDS DS core options for DSi/native/direct boot,
  auto-NAND selection, and disabled DSi SD card support.
- BIOS readiness checks both the RetroArch system root and `system/melonDS DS`.

## Required System Files

The validated DSi setup currently tracks:

- `bios7.bin`
- `bios9.bin`
- `firmware.bin`
- `dsi_bios7.bin`
- `dsi_bios9.bin`
- `dsi_firmware.bin`
- `dsi_nand.bin`
- `DSi_Nand_EUR.bin`
- `DSi_Nand_JPN.bin`

USA and Europe DSiWare smoke tests are validated. Japan NAND is installed and
tracked for region selection, but Japan runtime smoke coverage should still be
expanded when we test a Japanese title intentionally.

## Design Principle

DSi support should remain isolated behind the `melonds_dsiware` strategy. Do not
spread DSi-specific archive extraction, NAND handling, or core-option behavior
into the standard libretro installer path.
