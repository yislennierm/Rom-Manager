# MiSTer ROMs Manager

Open **Scripts → roms_manager** to browse your assigned catalogs, search, queue,
and install supported SNES/NES/Mega Drive ROMs. Gamepad navigation uses MiSTer's existing
Select/Back mappings; keyboard arrows, Enter, and Escape also work. Search text
requires a keyboard. **Display: Sofa/Desktop** changes the text size and remembers
the preference. The script restores the terminal and original font when it exits.
It never launches cores, changes controller mappings, or modifies updater files,
saves, BIOS, or other games.

## Preview locally before deployment

Use the real `MisterUI` and native `dialog` renderer on the development computer,
inside a private pseudo-terminal streamed to a localhost browser. The TV viewport
is 1280×720: Sofa uses 53×22 cells and Desktop 160×45. Keyboard arrows, Enter,
Escape, Tab, and search text work; on-screen navigation buttons are also available.
The UI's own display toggle changes the terminal dimensions too.

Host setup (no MiSTer changes):

```sh
bash mister/setup_preview.sh
```

This adds `pyte` only to the host virtualenv and, when needed, builds
[dialog](https://invisible-island.net/dialog/) inside ignored
`mister/.preview-tools/`. The helper uses the 1.3-20200327 release verified on the
current MiSTer. On macOS the helper adjusts host-only pointer-cache ordering
to avoid an Apple `tsearch` slowdown with large lists; it does not modify the
device binary or rendering. No `make install` or system-wide package installation is performed.
Set `PREVIEW_PYTHON` if using a different virtualenv. Linux hosts need a compiler,
make, and ncurses development headers; macOS needs Command Line Tools.

Capture a metadata snapshot with your existing authorized SSH access:

```sh
backend/.venv/bin/python -m mister.preview --capture root@MISTER_IP
```

This copies only the active catalog JSON/index files and public status metadata.
It does not copy `config.json`, API keys, ROM payloads, or device queue state, and
does not write client files on MiSTer. Capturing into a nonempty snapshot directory
is refused; use `--snapshot mister/.preview-cache-new` to retain an older snapshot.
SSH options may be passed as repeated `--ssh-option` values. Existing SSH keys,
agent, and configuration are used; optional `SSHPASS` is passed to `sshpass -e`
without putting its value in process arguments or storing it in the snapshot.

Alternatively, sync catalog/provider metadata directly with the normal client API:

```sh
backend/.venv/bin/python -m mister.preview --sync-catalog --config mister/config.json
```

Start the preview, then open <http://127.0.0.1:8766>:

```sh
backend/.venv/bin/python -m mister.preview
```

Options include `--mode desktop`, `--port 8767`, `--snapshot PATH`, and
`--dialog /path/to/dialog`. The listener binds to loopback only. Reload the page to
retain the current UI state, or choose **Restart** to reset the preview session.
Stop with Ctrl+C. Restart the command after editing Python UI code.

Browsing/search uses the copied real catalog. Queueing, retries, and transfer
progress run entirely in memory and are **simulated**: no provider download,
backend queue mutation, installation, or deployment can occur through preview
navigation. A new session starts with an empty queue. The real terminal widgets,
screen flow, and cell layout are shared with the client; the browser font and
physical gamepad input still require a final device check. Compatibility labels
come from the snapshot, so missing host extraction tools do not alter the preview.

## Client architecture and offline use

MiSTer follows the computer client's model: sync account metadata/catalogs once,
then browse the local catalog and download directly from providers. A running
backend is **not required** for cached browsing, search, queueing, retries, or
provider downloads. Internet access is still needed for provider files that have
not been cached. Verified downloads and source archives are cached for reuse.

- **Refresh catalog** uses the desktop client contracts: `/client/sync`,
  `/update?target=providers`, the account-filtered `/cache/archive`, assigned
  `/roms` metadata, and catalog
  pages of up to 500 entries. The text client requests `include_artwork=false`.
  A complete new snapshot atomically replaces the previous snapshot. Failed or
  interrupted refreshes retain the previous complete catalog and resume from
  a saved page checkpoint. During the first sync, already cached pages can be
  browsed while the remainder downloads.
- While the UI is open, a background thread refreshes metadata approximately
  every 30 seconds. It stops with the app; no boot service or daemon is added.
  All menu browsing, scrolling, filtering, and search read the local index.
  Provider matching and preference use the existing desktop TUI functions in
  `utils/catalog.py`; MiSTer does not maintain a separate matching algorithm.
  Desktop-only imports are lazy so these functions run on stock MiSTer Python.
- New assignments appear after a successful refresh. Offline access uses the
  last synced assignments, just as with the desktop client. A confirmed 401/403
  blocks new downloads until access is restored and refreshed; a timeout or
  backend outage retains cached access. Removed assignments stop future downloads
  when synced. Already installed games are never deleted.
- Catalogs use indexed JSONL snapshots, and the local queue uses atomically
  replaced JSON. This works with the installed minimal Python, which has no
  SQLite extension. State is under `cache/<account-fingerprint>/` beside the
  config. No second credential store is created. Changing the API key or backend
  isolates the old cache; perform a new refresh after changing either.
- Web-queued jobs are imported when connected. Local jobs are published through
  the existing normal client API when connected; progress and completion are
  reconciled opportunistically. Backend downtime delays web status updates but
  never blocks local transfers. Unimported web jobs require a connection once.

With no initial cache and no backend, the menu remains usable and explains that
**Refresh catalog** needs a connection. It does not exit with a traceback.

## Setup

Run the existing backend and build its existing admin UI:

```sh
backend/.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix backend/ui ci
npm --prefix backend/ui run build
backend/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

In `/admin/` → **MiSTer FPGA**, generate a device config. The account is the normal
non-admin **mister-fpga** account in **Users & access**. Assign consoles there;
backend permissions remain authoritative on every server request. The legacy
config field `device_token` holds this account's normal API key. Never use an
administrator token. Regenerating the config rotates the key and requires a new
catalog refresh. Preserve the existing config when updating client code.

Use the backend computer's LAN address, not `localhost`. The backend only needs
to be reachable for catalog/account refresh and web queue reconciliation. Keep
`config.json` private and outside Git. On Unix filesystems use mode 600; stock
root-owned MiSTer FAT/exFAT uses mount-wide permissions. HTTP is supported on a
trusted LAN; TLS verification stays enabled for HTTPS.

Requires Linux, Python 3.8+, `dialog`, the installed console core, and its mounted
games folder. ZIP preparation uses the standard library; 7z sources require the
already installed `7za`, `7z`, `7zz`, or `7zr`. No pip packages are installed on
MiSTer. The verified device has Python 3.9.6, `dialog`, and `/media/fat/linux/7za`.

Copy the files below using your normal SSH/SFTP connection. Create directories
as needed; the small `core` package contains only portable standard-library code.

| Source | Destination relative to `/media/fat/Scripts/` |
| --- | --- |
| `mister/roms_manager.sh` | `roms_manager.sh` |
| `mister/roms_manager_client.py` | `.config/roms_manager/roms_manager_client.py` |
| `mister/roms_manager_ui.py` | `.config/roms_manager/roms_manager_ui.py` |
| `mister/console_display.py` | `.config/roms_manager/console_display.py` |
| `mister/gamepad_input.py` | `.config/roms_manager/gamepad_input.py` |
| `mister/catalog_cache.py` | `.config/roms_manager/catalog_cache.py` |
| `mister/offline_client.py` | `.config/roms_manager/offline_client.py` |
| `mister/local_state.py` | `.config/roms_manager/local_state.py` |
| `mister/library.py` | `.config/roms_manager/library.py` |
| `utils/__init__.py` | `.config/roms_manager/utils/__init__.py` |
| `utils/catalog.py` | `.config/roms_manager/utils/catalog.py` |
| `utils/internet_archive_auth.py` | `.config/roms_manager/utils/internet_archive_auth.py` |
| `core/__init__.py` | `.config/roms_manager/core/__init__.py` |
| `core/client_downloads.py` | `.config/roms_manager/core/client_downloads.py` |
| `core/archive_names.py` | `.config/roms_manager/core/archive_names.py` |
| `core/download_preparation.py` | `.config/roms_manager/core/download_preparation.py` |
| `core/mister_sources.py` | `.config/roms_manager/core/mister_sources.py` |
| Downloaded `config.json` | `.config/roms_manager/config.json` |

Make the launcher executable. Run **Refresh catalog** once while connected.
The initial full catalog sync can take several minutes; subsequent unchanged
catalogs are skipped. Previously cached catalogs remain available during refresh.

```sh
chmod +x /media/fat/Scripts/roms_manager.sh
python3 /media/fat/Scripts/.config/roms_manager/roms_manager_client.py --refresh-catalog
```

Useful commands:

```sh
# Interactive UI; launcher chooses this when attached to a terminal.
python3 /media/fat/Scripts/.config/roms_manager/roms_manager_client.py --ui
# Download the local persistent queue; no backend connection required.
python3 /media/fat/Scripts/.config/roms_manager/roms_manager_client.py --sync
# Inspect cores and storage without any network request.
python3 /media/fat/Scripts/.config/roms_manager/roms_manager_client.py --probe
# Optional legacy backend-managed transfer path, explicitly online.
python3 /media/fat/Scripts/.config/roms_manager/roms_manager_client.py --backend-sync
```

## Downloading and storage

Choose **Browse games** or **Search**, select a console and game, confirm
queueing, then download immediately or use **Download queued games** later.
Cancel/Escape never queues accidentally. **Downloads** shows local progress,
completed jobs, errors, and Retry. **Fetch web queue** explicitly imports web work.
**Status** shows the cached account, connection state, last sync, cores, and storage.

Each console opens its real library directly, with all catalog entries visible
by default, like the desktop TUI. The library fills the terminal and scrolls
through all cached titles, rather than displaying five-game pages. **Search**
searches the local index; a blank search restores all titles. **Ready** filters
to entries with a usable source, and **All** clears that filter. Arrow keys move
through games; Page Up/Down and Home/End provide faster keyboard navigation.
Tab moves to the action buttons. Returning from details keeps the selected game.
Long titles retain both the beginning and the region/revision at the end.
The Sofa/Desktop setting changes text density. Test/demo libraries are hidden when a real
library exists. **Ready** is an optional filter; unsupported
entries explain why they lack a usable source. SNES accepts `.sfc`, `.smc`, `.bin`; NES accepts
`.nes` with an iNES/NES 2.0 header. Mega Drive / Genesis installs `.md`, `.bin`,
and `.gen` into `games/MegaDrive/ROMs Manager`, matching the installed
[MegaDrive core](https://github.com/MiSTer-devel/MegaDrive_MiSTer/blob/master/README.md).
Only single-file installations are supported.
Torrents, custom provider login flows, FDS/BIOS flows, conversion, and automatic
core launching remain outside this adapter.

Source validation, archive selection, checksums, and matching are shared with
the backend's web download flow. MiSTer prepares its own selected ZIP/nested-ZIP
or per-game ZIP/7z locally. Exact member checksums are verified independently
from archive checksums. Per-game archives require their own checksum, conservative
unique ROM selection, and an immutable SHA-256 receipt for the resulting ROM.
Ambiguous/encrypted archives fail instead of guessing. Only selected members are
installed. Archives are bounded to 512 MiB and ROMs to 64 MiB; preparation checks
cache space, and installation reserves an additional 16 MiB.

Each console resolves its normal MiSTer mounted games folder independently,
respecting USB/network/CIFS/SD precedence. Optional config overrides:

```json
"games_roots": {
  "snes": "/media/fat/games/SNES",
  "nes": "/media/fat/games/NES",
  "genesis": "/media/fat/games/MegaDrive"
}
```

Legacy `games_root` applies to SNES only. Installations go under **ROMs Manager**
in the corresponding core's normal file browser. A core must already be installed.
Traversal, symlinks, cross-console destinations, and unsafe mounts are rejected.

One local transfer lock prevents concurrent installs. Downloads use partial files
on the destination filesystem; resume requires a strong validator and matching
HTTP 206 range, otherwise restart. Size/checksum verification precedes atomic
no-overwrite publication. Existing matching files complete without replacement;
conflicts preserve the original and fail visibly. Interrupted local work resumes
on the next sync without waiting for a backend lease. Verified source archives
and direct payloads are retained to support repeat offline installs. Cache storage
is not automatically evicted; remove disposable download cache files when needed,
keeping `catalog.json`, its referenced snapshots, and `queue.json`.

The MiSTer account key is sent only to the configured backend, never to provider
URLs, logs, screens, or subprocess arguments. Provider requests do not inherit
backend Internet Archive cookies. Sources requiring custom authentication explain
the limitation. Backend-only preparation remains available for the explicit legacy
path, including its configured provider authentication.

## Shared API and validation

Normal client API operations enforce account ownership and current console
assignments: `/client/downloads`, `/targets`, per-job `/retry`, `/recipe`, `/claim`,
and `/prepared`. Recipes expose only authorized catalog download metadata.
Prepared receipts require a valid active claim and cannot alter known direct-file
metadata or change an already prepared archive's size/hash/destination. Existing
progress endpoints preserve verification-before-completion rules. Web queue state
remains in the existing backend SQLite store; no backend authentication is moved
into the adapter.

```sh
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
backend/.venv/bin/python -m unittest discover -s tests -v
npm --prefix backend/ui run build
```

Tests include fake-dialog navigation/cancellation, account and console enforcement,
queue persistence/recovery, source validation, bounded archive selection,
checksums, conflicts, atomic installation, cached pagination/search, interrupted
refresh, backend outages, and cached-byte reinstall with all network calls blocked.
`python -m mister.demo` creates original generated SNES/NES homebrew fixtures;
`--system nes --zip` exercises ZIP preparation without third-party ROMs.

## Verified device results

On the connected MiSTer at `192.168.1.85`, the corrected client cached the complete
assigned catalogs: 30,114 NES entries, 7,696 SNES entries, and 7,335 Mega Drive
entries, plus the existing homebrew fixture libraries. These are historical counts
from the earlier offline check. Mega Drive installation is now supported as well.

With the actual backend process stopped:

- Cached catalog search selected **Blaster Master (USA)**. MiSTer downloaded the
  provider's NES collection archive directly, extracted the selected ROM locally,
  verified it, and installed 262,160 bytes to
  `/media/fat/games/NES/ROMs Manager/Blaster Master (USA).nes`.
  SHA-256: `bc527d5fc8000e3ef5e7bade10ed6f8300785ccf068b2a9d06869cd2c235aeb3`.
- A cached SNES selection downloaded and prepared its 7z source locally using
  the installed extractor. The matching existing file was preserved:
  `/media/fat/games/SNES/ROMs Manager/Contra III - The Alien Wars (U) [!].smc`,
  1,048,576 bytes, SHA-256
  `a93ea87fc835c530b5135c5294433d15eef6dbf656144b387e89ac19cf864996`.
- With **all Python HTTP download calls additionally forbidden**, MiSTer installed
  **Blades of Steel (USA)** from the already cached NES collection archive.
  Zero network calls occurred. The new file is 131,088 bytes at
  `/media/fat/games/NES/ROMs Manager/Blades of Steel (USA).nes`, SHA-256
  `316e4c086802318e53857e25c26f9c86098ac0073e9a200e3618bf484e24bb24`.
- An automated private device terminal exercised the actual installed `dialog`:
  main menu → consoles → NES library → cached games → Back/Escape → clean exit.
  Raw joystick discovery was forbidden to preserve MiSTer's native input path.
  This is actual-device terminal automation, not a new physical sofa/gamepad test.

The existing account/config hash was unchanged. No core was launched; gameplay
and physical file-browser visibility were not rechecked during these offline
checks. The files are in the normal cores' games folders. The backend was restored
following the checks, and the two new NES completions reconciled to the web queue
through the same non-admin client API key. Local automated checks pass 135 tests; the React UI builds.


The shared TUI matcher now keeps beta/prototype/collection variants distinct
from retail entries. Both clients use this correction; catalog and provider
checksums remain separate.

## TUI library port and Sega verification

The MiSTer browser now follows the desktop TUI: choose an assigned console and
open its full real library directly. When a real catalog exists, generated demo
libraries do not intercept navigation. All games are shown by default, with an
optional downloadable-only filter. GUID-based backend lookup also prefers the
real master catalog over test fixtures, while explicit demo slugs still work.

MiSTer syncs the same permitted provider registry and cache archive used by the
computer client. Both clients call `utils.catalog.match_catalog_entries` and use
the same provider preference and access rules. Backend enrichment attaches those
matches without overwriting master RDB files. The configured Sega provider's
metadata/export was refreshed through the existing provider service; no new
provider or API credential was introduced.

On the connected device, the real dialog flow was verified as main menu →
consoles → Mega Drive / Genesis games → Back → exit, without a demo-library menu.
This was private-terminal automation using MiSTer's native keyboard input path;
physical gamepad interaction and gameplay were not newly tested.

With the backend process stopped, the device downloaded and locally prepared
**Sonic The Hedgehog (USA, Europe)** from the real Sega catalog. Installed:

`/media/fat/games/MegaDrive/ROMs Manager/Sonic The Hedgehog (USA, Europe).md`

Size: 524,288 bytes. MD5 matches the selected retail RDB entry:
`1bc674be034e43c96b86487ac69d9293`.
SHA-256: `46160baa06362c711c9f1a5017cb7371026444936c8af5e93a78996cf32ff2a6`.
The beta artifact from the earlier validation attempt was removed after checking
its exact filename and SHA-256. Existing games and configuration were preserved.

After restoring the backend, the normal client API reconciled the retail Sega job
to **completed**, with the retail filename and verified size. A subsequent catalog
refresh was interrupted by a lost SSH connection; the prior complete library
remained intact and the saved page checkpoint supports resuming it.
