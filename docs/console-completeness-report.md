# Console Completeness Report

Generated from backend module metadata, enriched ROM datasets, provider links, and console progress state.
This report is filtered to console-family systems for the current batch; computers, arcade buckets, and engines are intentionally deferred.

- Console-family modules assessed: 78
- Average backend/TUI completion score: 77.6%
- Runtime validated: 68
- Backend ready: 2
- Needs backend work: 8

Scoring is pragmatic: 100 means runtime validated with full provider coverage, 90 runtime validated with partial provider coverage, 75 runtime validated but provider links are not currently in the enriched backend dataset, 70 has provider-linked ROMs but no runtime proof, 35 has only a backend ROM dataset/provider shell, and 10 is effectively unstarted or blocked.

## 10%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| Microsoft - Xbox | `needs_backend_work` | - | 0 | providers, rom_dataset, core_mapping, runtime_test |
| Microsoft - Xbox 360 | `needs_backend_work` | - | 0 | providers, rdb, rom_dataset, core_mapping, runtime_test |
| Nintendo - Nintendo 3DS (DLC) | `needs_backend_work` | - | 0 | providers, rdb, rom_dataset, core_mapping, runtime_test |
| Nintendo - Wii U | `needs_backend_work` | - | 0 | providers, rdb, rom_dataset, runtime_test |
| Sony - PlayStation 3 | `needs_backend_work` | - | 0 | providers, rom_dataset, core_mapping, runtime_test |
| Sony - PlayStation 3 (Downloadable) | `needs_backend_work` | - | 0 | providers, rdb, rom_dataset, core_mapping, runtime_test |
| Sony - PlayStation 4 | `needs_backend_work` | - | 0 | providers, rdb, rom_dataset, core_mapping, runtime_test |

## 35%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| Sony - PlayStation Vita | `backend_ready_external_import_validated` | 119/239 RDB + 903 provider-only | 1 | automatic_vita3k_launch, external_emulator_tui_ux, partial_rdb_coverage |

## 70%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| LeapFrog - Leapster Learning Game System | `backend_ready` | 55/118 (46.6%) | 1 | runtime_test, partial_coverage |
| Sony - PlayStation 2 | `backend_ready` | 2515/11183 (22.5%) | 29 | runtime_test, partial_coverage |

## 75%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| Atari - Lynx | `runtime_validated` | 0/988 (0.0%) | 4 | coverage |
| Bandai - WonderSwan | `runtime_validated` | 0/241 (0.0%) | 5 | coverage |
| Bandai - WonderSwan Color | `runtime_validated` | 0/235 (0.0%) | 5 | coverage |
| Casio - Loopy | `runtime_validated` | 0/21 (0.0%) | 4 | coverage |
| Casio - PV-1000 | `runtime_validated` | 0/13 (0.0%) | 5 | coverage |
| Coleco - ColecoVision | `runtime_validated` | 0/307 (0.0%) | 7 | coverage |
| Emerson - Arcadia 2001 | `runtime_validated` | 0/70 (0.0%) | 4 | coverage |
| Entex - Adventure Vision | `runtime_validated` | 0/4 (0.0%) | 3 | coverage |
| Epoch - Super Cassette Vision | `runtime_validated` | 0/33 (0.0%) | 2 | coverage |
| Fairchild - Channel F | `runtime_validated` | 0/50 (0.0%) | 3 | coverage |
| Funtech - Super Acan | `runtime_validated` | 0/12 (0.0%) | 2 | coverage |
| GCE - Vectrex | `runtime_validated` | 0/74 (0.0%) | 7 | coverage |
| GamePark - GP32 | `runtime_validated` | 0/28 (0.0%) | 3 | coverage |
| Hartung - Game Master | `runtime_validated` | 0/18 (0.0%) | 1 | coverage |
| Nintendo - Game Boy | `runtime_validated` | 0/4399 (0.0%) | 4 | coverage |
| Nintendo - Game Boy Advance | `runtime_validated` | 0/4169 (0.0%) | 4 | coverage |
| Nintendo - Game Boy Color | `runtime_validated` | 0/2984 (0.0%) | 5 | coverage |
| Nintendo - GameCube | `runtime_validated` | 0/3808 (0.0%) | 5 | coverage |
| Nintendo - Nintendo 64DD | `runtime_validated` | 0/30 (0.0%) | 5 | coverage |
| RCA - Studio II | `runtime_validated` | 0/21 (0.0%) | 4 | coverage |
| SNK - Neo Geo Pocket Color | `runtime_validated` | 0/191 (0.0%) | 4 | coverage |
| Sega - 32X | `runtime_validated` | 0/240 (0.0%) | 8 | coverage |
| Sega - Dreamcast | `runtime_validated` | 0/1660 (0.0%) | 10 | coverage |
| Sega - Game Gear | `runtime_validated` | 0/1071 (0.0%) | 10 | coverage |
| Sega - Master System - Mark III | `runtime_validated` | 0/1440 (0.0%) | 7 | coverage |
| Sega - Mega Drive - Genesis | `runtime_validated` | 0/7400 (0.0%) | 4 | coverage |
| Sega - Mega-CD - Sega CD | `runtime_validated` | 0/425 (0.0%) | 1 | coverage |
| Sega - PICO | `runtime_validated` | 0/448 (0.0%) | 4 | coverage |

## 90%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| Amstrad - GX4000 | `runtime_validated` | 63/112 (56.2%) | 3 | partial_coverage |
| Arduboy Inc - Arduboy | `runtime_validated` | 463/464 (99.8%) | 2 | partial_coverage |
| Atari - 2600 | `runtime_validated` | 1359/1542 (88.1%) | 12 | partial_coverage |
| Atari - 5200 | `runtime_validated` | 290/296 (98.0%) | 9 | partial_coverage |
| Atari - 7800 | `runtime_validated` | 452/524 (86.3%) | 13 | partial_coverage |
| Atari - Jaguar | `runtime_validated` | 164/549 (29.9%) | 7 | partial_coverage |
| Commodore - CD32 | `runtime_validated` | 412/501 (82.2%) | 4 | partial_coverage |
| Magnavox - Odyssey2 | `runtime_validated` | 131/135 (97.0%) | 4 | partial_coverage |
| Mattel - Intellivision | `runtime_validated` | 281/366 (76.8%) | 4 | partial_coverage |
| NEC - PC Engine - TurboGrafx 16 | `runtime_validated` | 470/530 (88.7%) | 6 | partial_coverage |
| NEC - PC-FX | `runtime_validated` | 78/79 (98.7%) | 4 | partial_coverage |
| Nintendo - Family Computer Disk System | `runtime_validated` | 709/716 (99.0%) | 4 | partial_coverage |
| Nintendo - Nintendo 3DS | `runtime_validated` | 1567/2076 (75.5%) | 4 | partial_coverage |
| Nintendo - Nintendo 64 | `runtime_validated` | 1172/1475 (79.5%) | 4 | partial_coverage |
| Nintendo - Nintendo DS | `runtime_validated` | 1037/7667 (13.5%) | 4 | partial_coverage |
| Nintendo - Nintendo Entertainment System | `runtime_validated` | 4962/30114 (16.5%) | 3 | partial_coverage |
| Nintendo - Pokemon Mini | `runtime_validated` | 40/73 (54.8%) | 2 | partial_coverage |
| Nintendo - Satellaview | `runtime_validated` | 487/565 (86.2%) | 3 | partial_coverage |
| Nintendo - Super Nintendo Entertainment System | `runtime_validated` | 5550/7696 (72.1%) | 3 | partial_coverage |
| Nintendo - Virtual Boy | `runtime_validated` | 31/65 (47.7%) | 2 | partial_coverage |
| Nintendo - Wii | `runtime_validated` | 304/12126 (2.5%) | 3 | partial_coverage |
| Philips - CD-i | `runtime_validated` | 645/2404 (26.8%) | 3 | partial_coverage |
| SNK - Neo Geo | `runtime_validated` | 222/278 (79.9%) | 5 | partial_coverage |
| Sony - PlayStation | `runtime_validated` | 1455/13507 (10.8%) | 2 | partial_coverage |
| Sony - PlayStation Portable | `runtime_validated` | 4598/6207 (74.1%) | 5 | partial_coverage |
| The 3DO Company - 3DO | `runtime_validated` | 620/638 (97.2%) | 2 | partial_coverage |
| VTech - CreatiVision | `runtime_validated` | 26/31 (83.9%) | 1 | partial_coverage |
| VTech - V.Smile | `runtime_validated` | 227/455 (49.9%) | 2 | partial_coverage |
| Watara - Supervision | `runtime_validated` | 73/99 (73.7%) | 2 | partial_coverage |

## 100%

| Console | Status | Coverage | Providers | Gaps |
| --- | --- | ---: | ---: | --- |
| Commodore - CDTV | `runtime_validated` | 61/61 (100.0%) | 3 | - |
| NEC - PC Engine CD - TurboGrafx-CD | `runtime_validated` | 498/498 (100.0%) | 7 | - |
| NEC - PC Engine SuperGrafx | `runtime_validated` | 5/5 (100.0%) | 5 | - |
| Nintendo - Nintendo DSi | `runtime_validated` | 1068/1068 (100.0%) | 4 | - |
| Nintendo - Sufami Turbo | `runtime_validated` | 13/13 (100.0%) | 2 | - |
| Philips - Videopac+ | `runtime_validated` | 32/32 (100.0%) | 2 | - |
| SNK - Neo Geo CD | `runtime_validated` | 111/111 (100.0%) | 3 | - |
| SNK - Neo Geo Pocket | `runtime_validated` | 9/9 (100.0%) | 4 | - |
| Sega - SG-1000 | `runtime_validated` | 217/217 (100.0%) | 5 | - |
| Sega - Saturn | `runtime_validated` | 2253/2253 (100.0%) | 6 | - |
| Tiger - Game.com | `runtime_validated` | 23/23 (100.0%) | 3 | - |
