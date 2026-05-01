# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.5)

Fifth alpha. Five real fixes shipped: the alpha-0.4 "no buttons" regression, the cold-boot black screen, split-screen P2 view rendering nothing on the right half, P2's PLAYER_STATE never broadcast, and a clean centered post-race leaderboard with a START-to-skip option.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` (`[server:199406]`). The server is already live; just dial in from the Saturn.

## How to play

Load `game.cue` (not `game.iso` directly) on your emulator or ODE. From the title screen pick **Online Play**, enter a name (1–8 characters, persisted via Saturn backup RAM key `MMM_NAME`), and dial **199406**. Press A or C to ready up, START to request a race, server picks a random track from the 16-track roster.

## Critical fixes shipped in this build

| Bug | Root cause | Fix |
|---|---|---|
| **Black screen after Sega splash on cold boot** (alpha-0.5 first build regression) | sh-elf-gcc 9.3.0 with default `-O2` produced a 326 KB cd/0.bin. The user's working alpha-0.4 binary was 316 KB. Anything above ~317 KB black-screens on the user's Saturn hardware before the title screen renders — the exact failure mode is unclear (the binary loads well within Work RAM-H), but the empirical threshold is consistent. The 10 KB regression came from sprintf/printf machinery pulled out of newlib's libc.a for client-log diagnostics + the new code added in this release. | `CCFLAGS += -Os` in Makefile (after the `include` line) overrides the default `-O2` and trades a small amount of perf for ~17 KB of binary shrinkage. Build is now 309 KB, comfortably under the threshold. Combined with libc_stubs.c stubbing every `lib_a-syscalls.o` symbol so that archive member is never pulled (saves another 10 KB). |
| **No buttons work for ANY player in online race** (alpha-0.4 regression) | `mmm_online_start_race` populated `s_is_local[]` from `g_mnet.lobby_players[p].is_local`, but `process_game_start` (mmm_net.c:318) memsets `lobby_players` to 0 before the race-start fires. Every `is_local` field read back as `false`. The fallback resolution loop also failed because it depends on `s_net_player_id[]` which is `MNET_INVALID_PLAYER_ID` after the wipe. With `s_is_local[user_slot]=false`, `my_gamepad`'s line 5577 (`if (g_online_mode && !s_is_local[p]) players[p].gamepad = 7;`) overrode the pinned port-0 mapping on every frame, so KEY_PRESS read port 7 (unused) and returned 0 for everything. | Use pid-as-slot-index directly. Server assigns pids sequentially 0..total-1 (primaries first, then each client's local-coop P2's, then bots — mserver.py:1759), so `s_net_player_id[i] = i` is correct, and `s_is_local[my_player_id] = true` resolves the user's slot without needing the wiped lobby state. |
| **Split-screen P2 view (right half) shows no 3D + no map / cars** in online local-coop | The `slCurWindow(winNear)` block in `my_draw()` was gated on `game.mode == GAMEMODE_2PLAYERVS`, which is the offline 2-player mode. Online race uses `GAMEMODE_NETLINKRACE`, so the right-half render block was skipped entirely; only the global VDP2 sky/floor scroll layers showed through, no map polygons or cars. | New `rt` (right-half target) variable: offline `2PLAYERVS` → slot 1, online local-coop → `g_mnet.my_player_id_2` (the pid the server assigned the local-P2 — usually slot 2 because primaries are pid 0..N-1 and P2's come after). Block now renders for both modes, and pulls camera + map + powerup distances from the right slot instead of hardcoded slot 1. |
| **P2 PLAYER_STATE never broadcast** — other Saturns can't see your local-coop P2 driving | The P2 producer block was deferred ("factor into mmm_net.c") and main.c's online send block only ever called `mnet_send_player_state` for P1. Servers tracked P2 only via implicit-lap-from-PLAYER_STATE which never fired because we never sent P2 state. | Added P2 send right after the P1 send in `my_gamepad`. Pulls position/lap/checkpoint/waypoint from `players[g_mnet.my_player_id_2]` and calls `mnet_send_player_state_p2` (which has its own internal cooldown so the per-frame call still throttles to ~7.5 Hz). |
| **Race-end leaderboard rendered as bare unaligned text and auto-skipped after 4s** | Inline `jo_nbg2_printf` calls without column alignment, no DNF marker, no skip control, RACE_END_TIMER was 120 frames. | Centered `- RACE COMPLETE -` banner, header row with dashed divider, position labels (1ST/2ND/3RD/4TH/DNF), time formatted as `M-SS` (NBG2 font has no `:` so we use `-`), live countdown `RETURNING TO LOBBY IN N`, and `PRESS START TO SKIP` for impatient players. RACE_END_TIMER bumped to 240 (8 s) so there's actually time to read the standings. |
| **Build failure: multiple definition of `_sbrk`** (this Windows toolchain) | newlib's `libc.a(lib_a-syscalls.o)` provides `_sbrk` *and* a bunch of other syscalls. Just removing our `_sbrk` pulls the whole `lib_a-syscalls.o` in for ~10 KB of dead syscall code; defining only `_sbrk` collides on the multi-def. | Stub every symbol newlib's `lib_a-syscalls.o` provides (`_sbrk`, `_read`, `_write`, `_close`, `_open`, `_fstat`, `_isatty`, `_lseek`, `_kill`, `_getpid`, `_exit`) so no symbol from that translation unit is needed → archive member never pulled → no multi-def, no dead code. |

## What's IN alpha 0.5

- All alpha 0.4 functionality (server-side stall detection, race-always-terminates safeties, lap counter visibility)
- **Title boots cleanly on cold boot** (linker fix)
- **Controls actually work for primary AND local-coop P2** (real fix this time)
- **Split-screen renders both halves in online local-coop**
- **Other Saturns now see your P2's car driving in real time**
- **Crisp post-race leaderboard** with position/name/time columns, DNF marker, and START-to-skip

## Known limitations carried into alpha 0.6

- **Car selection lost in lobby→race transition** — `process_game_start` wipes `lobby_players[]` so `mmm_online_start_race` reads `car_id=0` for every slot. All cars default to car 0 in the race. Fix path: capture car selections into the surviving `game_roster[]` (currently only stores id+name).
- **Bot AI still uses procedural waypoints, not the actual track geometry.** Real per-track waypoints live in the `.bin` files on disc and aren't uploaded to the server yet.
- 5-player+ matches not exercised yet (cap is `MNET_MAX_PLAYERS = 4`).

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, tracks, cars, physics, powerups, AI, and audio — is entirely the work of jberetta. Please credit them if you share gameplay.
