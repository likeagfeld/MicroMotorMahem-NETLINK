# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.1)

First alpha release of online multiplayer support for MicroMotorMayhem via the Sega Saturn NetLink modem.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` alongside the other NetLink revival games (`[server:199406]` → `saturncoup.duckdns.org:4826`). The server is already live and running; just dial in from the Saturn and the bridge takes care of the rest.

## How to play

Load `game.cue` (not `game.iso` directly) on your emulator or ODE. From the title screen pick **Online Play**, enter a name (1–8 characters, persisted across power cycles via Saturn backup RAM), and dial **199406**. The bridge connects you to the lobby; press A or C to ready up, START to request a race, and the server picks a random track from the full 16-track roster.

## What's in this alpha

The online flow has been kept deliberately minimal so the underlying protocol and engine integration can be hardened first; some features the full game will eventually grow (track voting, server-side bots with real per-track waypoint geometry, spectator camera, reconnect mid-race) are stripped or stubbed in this build. The sections below describe **only what's actually in alpha 0.1** — not future plans.

### Online flow

- **Title screen → Online Play**: new menu entry on the title screen routes into the network flow
- **Name entry**: type a 1–8 character name (saved across sessions via Saturn backup RAM key `MMM_NAME`, so reconnecting keeps your name and leaderboard stats)
- **Connecting**: dial-up + auto-detect of NetLink modem; the connecting screen shows handshake progress and error states
- **Lobby**: see who's connected, cycle your car through the 8 available models, toggle ready, request a start
- **Race**: full MicroMotorMayhem 3D top-down racing on a server-picked random track, 3 laps, server-rolled powerups, server-validated finish detection
- **Results**: existing MMM `end_level()` results table, with the name source swapped from local to networked roster, then back to lobby

### Lobby controls (simplified)

- **A / C** — toggle READY
- **L / R** — cycle car (8 models)
- **START** — request race start
- **B** — back to title screen (stay connected for quick rejoin)
- **Y** — disconnect
- **Z** — hold for leaderboard overlay (WINS · BEST LAP · PODIUMS · RACES · POINTS)

That's it. Track voting, bot add/remove, disconnect-confirmation, and spectator-camera toggles — **all stripped for now** and can return in alpha 0.2 once the online flow is rock-solid.

### Match rules

- **3 laps**, matching the existing offline `MAX_LAPS` balance — gameplay tuning is deliberately untouched
- **Server picks the track at random** from all 16 tracks per match; no vote UI in this alpha
- **Server rolls powerup types** at pickup time (replacing the offline `jo_random(7)` call at `main.c:1491` which would have desynced) — every Saturn sees the same boost / bomb / rockets / shrink / shield / spring / invincibility result from any given crate
- **Server rolls CPU car selection** (replacing `jo_random(model_total-1)` at `main.c:6475`) so all peers see the same opponent line-up
- **Server-authoritative lap and checkpoint validation** — your Saturn drives the local car for zero-lag feel, but the server is the source of truth for "did you actually cross checkpoint N before checkpoint N+1" and "did you finish lap 3"
- **Tiebreak** uses MMM's existing `set_player_position()` logic at `main.c:702` (lap → checkpoint → distance to next waypoint) — same as offline single-player

### Co-op (P2 on the same Saturn)

- Plug a second controller into port B **in the lobby** — the server allocates a P2 slot for you and an `ADD_LOCAL_PLAYER` packet ships, no extra dial-in needed
- Both P1 and P2 are tracked as separate game players on the leaderboard
- Hot-unplug the 2nd pad mid-match and the Saturn cleanly reverts to single-camera fullscreen — `REMOVE_LOCAL_PLAYER` ack from server
- **Online mode does NOT use split-screen.** Both local players share one camera for clarity over the link. Saturn-side split-screen rendering is reserved for offline `GAMEMODE_2PLAYERVS` only

### Controls (in-game)

- **A** — Brake / Reverse
- **B** — Accelerate
- **C** — Horn
- **LT** — Use Powerup
- **RT** — Look behind
- **Y** — Change Camera
- **START** — Pause game

These are jberetta's existing controls — alpha 0.1 does not change in-game inputs at all.

### Networking

- **Wire**: SNCP-framed binary protocol over the NetLink 16550 UART → modem → DreamPi/PC bridge → TCP → game server (same transport as the Disasteroids / Flicky / Utenyaa siblings)
- **Sync model**: passthrough — local pad input drives local car movement (zero perceived lag); server relays input + position state to peers; remote cars interpolate to smooth jitter. Remote inputs are injected via the existing `cpu_left/right/gas/brake/action` flags on `players[]` so downstream physics and rendering are completely untouched
- **Authority split**: server owns lap/checkpoint validation, powerup type rolls, CPU car selection rolls, finish detection, race-start-timer, leaderboard. Client owns local car movement, local input, the entity render loop, and the HUD
- **Determinism**: `srand(game_seed)` from the `GAME_START` packet; powerup-type and CPU-car rolls are server-broadcast values, not local `jo_random()` calls. Float physics are kept as-is — passthrough sync corrects drift via periodic `PLAYER_SYNC` lerp

### Persistence

- **Saturn backup RAM**: 8-character player name persists across power cycles under cartridge key `MMM_NAME`, so the server leaderboard recognises you across sessions
- **Server leaderboard**: JSON-backed file on the server, top players by **WINS / BEST LAP / PODIUMS / RACES / POINTS** — `POINTS` uses MMM's existing `points_table[] = {0, 4, 3, 2, 1}`, `BEST LAP` shares the same `level_data[].level_fastest_lap` field used by offline time-attack

## Critical fixes shipped in this build

| Bug | Root cause | Fix |
|---|---|---|
| Build failed: `undefined reference to jo_nbg2_print` and `jo_vdp2_set_nbg2_8bits_font` | Pre-built `joe/jo_engine/*.o` were compiled with `JO_COMPILE_WITH_PRINTF_SUPPORT` defined, which `#ifndef`-excludes the nbg2 functions used heavily by MMM's HUD and lobby text. The bundled `D:/joengine-master/` jo_engine differs from the version MMM was originally built against. | Vendor `jo_engine/` source locally into the project root and rebuild from source with `PRINTF=0` + `AUDIO=1`, mirroring how Utenyaa vendors its jo_engine source for hermetic builds. Both `jo_nbg2_print` and `jo_audio_init` now resolve from the same vendored tree. |
| Build failed: `saved_gamepad undeclared` in `cpu_control()` | A `players[p].gamepad = saved_gamepad;` restore line was placed in `cpu_control()` where the local `saved_gamepad` doesn't exist; the actual save/restore lives in `my_gamepad()`. | Removed the orphan restore from `cpu_control()`; added a properly-scoped restore at the end of `my_gamepad()`'s player loop, gated on `g_online_mode && !s_is_local[p]` so it only fires for remote slots. |
| Build failed: `GAMESTATE_TITLE_SCREEN undeclared` in `name_entry.c` | New screen files (`name_entry.c`, `connecting.c`, `lobby.c`) include only `state.h`, not `hamster.h`, but referenced legacy state constants from the upstream enum. | `state.h` now shadows the existing `GAMESTATE_*` constants (`TITLE_SCREEN`, `GAMEPLAY`, `RACE_START`, `END_LEVEL`) with `#ifndef` guards so the new screen files stay decoupled from `hamster.h` and don't pull the entire game header into network code. |

## Known limitations & next steps (alpha 0.2)

- **Track voting UI** — stripped for alpha 0.1; server picks a random track per match. Voting will return as a polished UI element once the core flow is locked.
- **Server-side bots default to 0** — the AI logic is a faithful Python port of MMM's `cpu_control()` waypoint-follower, but the waypoint geometry currently uses procedural stubs because the real per-track waypoints live inside the track `.bin` files. Server-side `.bin` parsing is deferred to alpha 0.2; until then, lobbies fill with humans only.
- **Spectator follow-leader camera** — planned but not exercised; eliminated/finished players currently watch their own ghost car finish.
- **Reconnect mid-race** — if your modem drops mid-race the server will keep your slot for a few seconds but the client doesn't yet know how to rejoin a race in progress; you'll land back in the lobby.
- **Disconnect confirmation** — a single Y-press disconnects with no "are you sure?" overlay; easy to leave by accident. Will add a Y-twice gating in alpha 0.2.
- **5-player+ matches not exercised** — cap is `players[4]` from the upstream engine; we're honouring it.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, all 16 tracks, all 8 cars, the physics, the powerups, the HUD, the AI, the audio — is entirely the work of jberetta. Please credit them if you share gameplay. This is an alpha; expect rough edges, and please report what you find.
