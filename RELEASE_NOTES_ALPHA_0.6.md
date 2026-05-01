# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6)

Sixth alpha. The big one — the alpha-0.5 "race never ends" mystery is finally solved (it was dead code; PLAYER_STATE was never reaching the wire), plus three rendering bugs that were stacking up across race transitions.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` (`[server:199406]` → `saturncoup.duckdns.org:4826`). The server is already live; just dial in from the Saturn.

## How to play

Load `game.cue` (not `game.iso` directly). From the title screen pick **Online Play**, enter a name (1–8 characters, persisted via Saturn backup RAM key `MMM_NAME`), and dial **199406**. Press A or C to ready up, START to request a race, server picks a random track from the 16-track roster.

## Critical fixes shipped in this build

| Bug | Root cause (from server log diagnostics) | Fix |
|---|---|---|
| **Race never ended after completing 3 laps** (the recurring nightmare) | The entire online plumbing block — PLAYER_STATE send, race-finish detection, PLAYER_SYNC consumer, frame counter — was misplaced inside `cpu_control()` since alpha 0.1. `cpu_control()` returns early at line 5340 if `game.mode != GAMEMODE_1PLAYERRACE && game.mode != GAMEMODE_1PLAYERSURVIVAL`. Online mode is `GAMEMODE_NETLINKRACE`, so the block was **dead code from day one**. Server received zero PLAYER_STATE messages every race; lap counter on server stayed at 0 forever; race always ended via 180s stall-DNF. Discovered by adding diagnostic logging — `DIAG_P1 INIT/HB/LAP` never fired in the test logs because the function containing them was never reached. | Moved the entire online block into `my_gamepad()`, which only filters by `game.game_state` (`GAMEPLAY`/`RACE_START`), not by `game.mode`. Server now receives PLAYER_STATE at 7.5 Hz, sees lap progression, fires `Race finished!` on the lap-3 finish instead of stall timeout. |
| **P2 local-coop controls didn't work** | `mmm_get_p2_port()` was using `jo_is_input_available()` which queries jo_engine's *cooked* `jo_inputs[]` array — `jo_inputs[6]` maps to `Smpc_Peripheral[15]` per joengine's input.c remapping (offset 54 ints). But `KEY_PRESS(id, key)` reads `Smpc_Peripheral[id]` directly with the *raw* SBL index. So `KEY_PRESS(6, ...)` read an empty slot while the actual P2 controller sat at index 15. Confirmed via diagnostic dump: `DIAG_PER ids=16 FF FF FF _ _ FF _ _ _ _ _ _ _ _ 02` — P2 (id=0x02) at slot 15. | `mmm_get_p2_port()` now probes `Smpc_Peripheral[15]` (port B no-multitap, the slot upstream MMM offline-2P uses), then `[1]` (port A multitap slot 2), then `[8]` (port B multitap slot 1). Plus a self-healing re-pin in `mmm_network_tick()` that runs every frame: if `LOCAL_PLAYER_ACK` arrives AFTER `mmm_online_start_race` (verified 4-second gap in race log) and `s_is_local[my_player_id_2]` was therefore `false`, the re-pin fixes it next frame and logs `DIAG_REPIN`. |
| **Disconnecting from lobby → title showed leftover track instead of title 3D background** | Lobby/connecting/name_entry exit paths called `mmm_set_game_state(GAMESTATE_TITLE_SCREEN)` directly — that's a one-liner that just writes to `game.game_state`. It didn't reload `TITLE.BIN` / `TITLE.TGA` or reset the 3D plane, so the previous race's track sprites and geometry stayed on screen behind the title menu. | All four exit sites (lobby B, lobby Y, lobby on-disconnect, connecting fail timeout, name_entry B) now call `transition_to_title_screen()` which does the proper `load_level()` for level 0 (TITLE) plus `init_1p_display()` and `ztClearText()`. |
| **Subsequent races (after returning to lobby) had broken track rendering** | After race 1 ends, `preview_tex` and `trackmap_tex` globals held sprite IDs > `game.map_sprite_id` from the previous race. Race 2's `mmm_online_start_race` runs `jo_sprite_free_from(game.map_sprite_id)` which invalidates everything past `map_sprite_id` — including the IDs in those globals. Then `load_preview()` and `load_trackmap()` call `jo_sprite_free_from(stale_id)` which is *usually* a silent no-op (stale > current `__jo_sprite_id`), but in some allocation orders the stale ID ended up *inside* the freshly-loaded TRACK.TGA range and freed out track tiles by accident. | Reset both globals to `game.map_sprite_id` immediately after the PHASE C `jo_sprite_free_from` so subsequent `load_preview/load_trackmap` calls always operate against valid state. |
| **Occasional powerup icon distortion at top-center HUD** | `PUP_TILESET=144` was a hardcoded magic number in objects.h based on the assumption that exactly 12 cars + 132 player textures get loaded before PUP.TGA. Any drift in those counts from upstream changes silently mis-points the runtime sprite-draw calls at wrong VRAM tiles. Same fragility class as the old `MAP_TILESET=175` issue. | Capture PUP base id dynamically: `game.pup_sprite_id = jo_sprite_add_tga_tileset(...)` in `load_player_and_enemies()`. All 5 runtime `jo_sprite_draw3D(PUP_TILESET+N, ...)` references now use `game.pup_sprite_id+N`. The compile-time `PUP_TILESET` macro is still defined for the static `ATTRIBUTE` arrays in objects.h (which require a constant) but no longer drives runtime draws. |

## What's IN alpha 0.6

- All alpha 0.5 functionality (controls, split-screen, leaderboard polish)
- **Race actually ends on lap 3** instead of stall-DNF (the big one)
- **P2 controls work** in online local-coop (real fix this time)
- **Title screen renders correctly** after returning from lobby
- **Subsequent races render correctly** without leftover sprite-atlas artifacts
- **Powerup icon doesn't get corrupted** even after main.c growth
- Diagnostic logging still on (`DIAG_PER`, `DIAG_GP`, `DIAG_P1 INIT/HB/LAP/CP`, `DIAG_P2`, `DIAG_REPIN`) — will be stripped in 0.7 once we've confirmed everything's stable

## Known limitations carried into alpha 0.7

- **Car selection lost in lobby→race transition** — `process_game_start` wipes `lobby_players[]` so all cars default to car 0 in the race
- **Bot AI uses procedural waypoints, not real track geometry**
- **5-player+ matches not exercised** (cap is `MNET_MAX_PLAYERS = 4`)
- Diagnostic logging still shipping — will strip in 0.7

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, tracks, cars, physics, powerups, AI, and audio — is entirely the work of jberetta. Please credit them if you share gameplay.
