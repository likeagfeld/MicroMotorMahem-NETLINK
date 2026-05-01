# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.2)

Second alpha. The 0.1 hardening pass: the online flow now actually completes a race start-to-finish on real Saturn hardware, with bots that are visible and hittable, and 2-player human-vs-human play where each Saturn correctly drives its own car.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` alongside the other NetLink revival games (`[server:199406]`). The server is already live; just dial in from the Saturn.

## How to play

Load `game.cue` (not `game.iso` directly) on your emulator or ODE. From the title screen pick **Online Play**, enter a name (1–8 characters, persisted across power cycles via Saturn backup RAM key `MMM_NAME`), and dial **199406**. Press A or C to ready up, START to request a race, server picks a random track from the full 16-track roster.

## Critical fixes shipped in this build

| Bug | Root cause | Fix |
|---|---|---|
| Title screen renders broken sprites + 3D background after online attempt | `lobby` / `connecting` "back to title" paths set `game_state = TITLE_SCREEN` directly without reloading `TITLE.BIN` / `TITLE.TGA`. `xpdata_` and `MAP_TILESET` slots still held the previous track's data. | Online → title transitions go via `transition_to_title_screen()` which reloads the title assets. |
| TITLE.BIN load freeze on real hardware (lobby → race-start) | Title-screen CDDA was still playing when the online flow tried to read TITLE-equivalent assets through `jo_fs_read_file`. Saturn CD block can't service ISO9660 reads while audio decoder owns the head. | Stop CDDA at the top of `mmm_online_start_race` — mirrors what `race_start()` does for the offline path. |
| Map textures glitched in patches during race; cars rendered as wheels-only | `mmm_online_start_race` was calling `load_car()` BEFORE `load_level()` overwrote `xpdata_` with track meshes. `load_car` then copied track geometry into `players_car[p]` with PLAYER_TILESET texture indices that pointed at unbound slots. | Mirror offline order exactly: `load_binary("CARS.BIN")` first → per-player `load_car()` (captures real car geometry into `players_car[]`) → THEN `load_level()` for the track. |
| Bot car invisible in race | PLAYER_SYNC packets stored at `remote_states[server_pid]`, but consumer was looking up by local slot index. With server-assigned bot pid=2 and bot in `players[1]` locally, lookup of `remote_states[1]` returned empty. | Lookup uses `s_net_player_id[p]` (the lobby_state-derived `slot → server-pid` map). Same fix applied to INPUT_RELAY consumer. |
| Race never ended (went 6 laps with no finish) | Saturn's `mnet_send_lap_complete()` was defined but never called. Server's `p.lap` stayed at 0 forever, `check_race_finish` never triggered. | Server now also accepts implicit lap progression from periodic PLAYER_STATE packets (`lap == p.lap+1`). Race-finish broadcasts to all clients on first player to reach `lap_count`. |
| 2-human play: only one player can control their car | `create_player` assigned `players[0].gamepad = 0` and `players[1].gamepad = 15` (offline 2P split-screen pattern). When the server gave the second-connecting Saturn pid=2, that user's car ended up in `players[1]` but their controller was on Port A (slot 0), so KEY_PRESS read from an empty Port B (15) and nothing moved. Camera and HUD also defaulted to `target_player=0` showing the wrong car. | After `create_player`, walk `s_net_player_id[]` to find OUR primary local slot; pin its gamepad to 0, set `target_player` to that slot. Local-coop P2 slot gets gamepad 15. |
| Pause-menu QUIT during online race went to LEVEL_SELECT | Case 6 unconditionally called `clear_level()` → `transition_to_level_select()`. | Online branch routes to `GAMESTATE_LOBBY` (stay connected, ready up for next race). Offline path unchanged. |
| Local P2 controller not registering in lobby | `mmm_get_p2_port` checked Smpc slots 1 and 6, but MMM uses slot **15** for Port B in 2-player mode (see `create_player` `pad_number += 15`). | Added slot 15 as the first check; falls back to multitap (1) and slot 6. |
| Cursor invisible in name entry; modem-dial messages garbled | NBG2 font (`main.c:7278`) maps only ` 0-9 A-Z !"?=%&',.()*+-/<>` — no lowercase letters, no `[`/`]`, no `:`. Every `[X]` cursor and lowercase log line rendered as garbage glyphs. | Cursor now uses `>X<` (both `<` and `>` ARE in the font). All `mnet_log()` and `build_log()` strings uppercased; `:` replaced with space or `-` in lobby/name-entry/connecting labels. |
| Race-start crash (`AttributeError: '_sync_round_robin'`) | Python server accessed `self._sync_round_robin` on `MMMServer` but the attribute lives on `GameSimulation`. | Use `self.sim._sync_round_robin` in `_game_tick`. |
| P2 add denied by server immediately on plug-in | Server sent `LOCAL_PLAYER_ACK(0xFF)` on lobby-time slot reservation; client treats `0xFF` as denial sentinel. | Removed bogus ack — lobby_state broadcast informs client; real ACK with assigned pid is sent at GAME_START time. |

## What's IN alpha 0.2

- Online flow: Title → Online Play → Name Entry → Connecting → Lobby → Race → Results → back to Lobby
- Server-authoritative race finish on first player to reach lap count, with full standings (lap > checkpoint > distance-to-next-waypoint tiebreak)
- Server-rolled powerup types (no client RNG desync)
- Random track selection (16-track roster)
- Persistent leaderboard JSON (WINS · BEST LAP · PODIUMS · RACES · POINTS)
- Backup-RAM name persistence (`MMM_NAME` cartridge key)
- Mixed local-coop + online (plug 2nd controller in lobby; hot-unplug reverts to single-camera fullscreen)
- 3D Control Pad support (peripheral id `0x16`): analog stick = steering, RT = gas, B = brake, LT = item use
- NetLink LED heartbeat during connecting / lobby / race-start / gameplay (board ctrl `0x25885031` bit 7)
- Client→server diagnostic logging with token-bucket rate limit (`mmm_client.log` rotated server-side)
- Online → title screen properly reloads TITLE.BIN/TITLE.TGA assets

## Known limitations & alpha 0.3 roadmap

- **Bot AI uses procedural waypoints, not the actual track geometry.** Real per-track waypoints live in the `.bin` files on disc and aren't uploaded to the server yet. Bots are visible, hittable, and consistent across consoles, but they steer in a generic oval pattern rather than following the actual track.3 fix.
- **HUD top-center distortion in some online matches** — sprite-slot misalignment that shows up only in some online sessions. Under investigation; possibly related to map_sprite_id reuse across track loads.
- **Split-screen P2 view missing 3D + background** during local-coop online — under investigation. The first viewport renders fine.
- **Local-coop P2 lap completion** is misattributed to P1 if `LAP_COMPLETE` packet (which has no pid byte) is the source. Server's implicit `PLAYER_STATE.lap` validation handles this for now, so race-finish is correct.
- 5-player+ matches not exercised yet (cap is `MNET_MAX_PLAYERS = 4`).
- Disconnect prompt is single-Y without confirmation.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, tracks, cars, physics, powerups, AI, and audio — is entirely the work of jberetta. Please credit them if you share gameplay.
