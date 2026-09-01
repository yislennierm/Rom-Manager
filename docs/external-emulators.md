# External Emulator Targets

ROMs Manager normally targets RetroArch/libretro frontends. Some newer systems do
not have a practical Flatpak RetroArch core and must be treated as standalone
runtime integrations instead.

## Sony - PlayStation Vita

- Console GUID: `219b39f7-8c82-5053-8efa-b74c7c654aa7`
- Runtime: Vita3K standalone AppImage
- Installed command on this workstation: `vita3k`
- Installed version checked: `Vita3K v0.2.1 4074-496939b6`
- Source: <https://github.com/Vita3K/Vita3K>
- Quickstart: <https://vita3k.org/quickstart.html>
- Official firmware source: <https://www.playstation.com/en-us/support/hardware/psvita/system-software/>
- Firmware installed locally through Vita3K: PS Vita System Software 3.74

Vita3K is not a libretro core, so this console should not be marked as ready for
the Flatpak RetroArch pipeline. Treat it as an external runtime until ROMs
Manager has an external-emulator install contract.

Vita3K requirements from the official quickstart:

- 64-bit operating system.
- Minimum GPU support: OpenGL 4.4.
- Recommended renderer: Vulkan.
- Some games require PS Vita firmware installed through Vita3K.
- Games should be user-dumped. Vita3K supports `.pkg`, VCI, NoNpDrm, FAGDec, and
  manually decrypted game formats; Vitamin dumps are not supported and Maidump is
  unstable.

ROMs Manager automation still needed:

- Automatic Vita3K import for `.vpk`, `.zip`, VCI, and folder installs.
- Secure `.pkg` handling. Vita3K requires zRIF/license material for package
  installs, and ROMs Manager does not store or automate those keys yet.
- Launch strategy for installed Vita3K title IDs.
- Cleanup strategy for installed Vita3K apps and staged test content.
- A stable legal provider source. The libretro master list currently contains
  commercial `.vpk` titles, but ROMs Manager should not add public commercial
  providers. Use user-owned dumps or a vetted homebrew source.

Implemented locally:

- External emulator detection for `vita3k`.
- A frontend profile that marks Vita3K as `external_emulator` instead of
  `retroarch`.
- Firmware readiness checks that run locally and do not expose local paths in
  backend-facing metadata.
- Staging-only TUI install behavior for Vita packages, with no RetroArch
  playlist generation.
- User-provided Internet Archive NoNpDRM USA provider metadata.
- Serial/title-ID matching for provider files named like `[PCSE00322]`, which
  currently maps 119 of 239 Vita RDB catalog entries.
- Provider-only catalog expansion for Vita. The same provider contributes 903
  additional downloadable entries that are not present in the libretro RDB, for
  1,142 total backend catalog rows.
- Vita3K package import was validated with `PCSE00322`; the test import and
  staged copy were cleaned up after validation.

Provider/runtime notes:

- The Internet Archive item exports 1,022 ZIP packages. Its torrent metadata
  returned HTTP 403 during fetch, but direct package URLs from `files.xml` are
  usable.
- Provider-only entries are opt-in per provider via `allow_provider_only`; this
  prevents broad archives for older consoles from flooding their catalogs.
- ZIP filenames must be staged with safe ASCII names before invoking Vita3K;
  Vita3K's CLI misparsed the original filename with spaces/special characters.
- Vita3K uses installed title IDs such as `PCSE00322` for `--installed-path`.
  ROMs Manager still needs a TUI launch UX for external emulator title IDs.
