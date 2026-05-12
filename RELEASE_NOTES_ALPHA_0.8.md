# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.8)

**Stability milestone.** Consolidates every fix from the 0.7.x line (0.7.0 → 0.7.2 iter-4) into a single clean release with a new tag and audited code. Online multi-player races now work end-to-end: lobby → race-start → smooth gameplay → race-finish → return to lobby, with mixed local-coop + online slots supported throughout. All wire-format changes from 0.7.x are baked in.

Massive thanks to [jberetta](https://github.com/jberetta82) for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## What's IN 0.8.0

### Network protocol & sync

- **Per-pid car roster atomic with GAME_START** — `[car_count:1][car_id:1]×N` appended to GAME_START payload so PHASE_B reads correct cars immediately, before any PLAYER_JOIN messages arrive. Fixed "all players rendering as same car" symptom across the 0.7.x line.
- **PLAYER_JOIN carries `car_id`** — trailing byte after the name string. Backward-compatible with pre-0.7.2 clients (trailing byte ignored).
- **Server-side `has_authoritative_pose` gate** — server skips PLAYER_SYNC broadcast for a pid until that pid's first PLAYER_STATE arrives. Eliminates the "remote player spawns at (0,0,0) world origin" rendering bug. Bots flip True at construction; humans flip True on first PLAYER_STATE.
- **PLAYER_SYNC rate doubled to 10 Hz/pid** — server broadcasts all authoritative pids every 2 ticks (was round-robin one pid per tick at 5 Hz/pid). Halves visible jump distance between snaps.
- **Server stops PLAYER_SYNC after `race_finished`** — prevents post-race packet flood from queueing ahead of RACE_FINISH, which caused the 5/1 21-second race-end delay (TOAST received RACE_FINISH 21 seconds after server detected lap=4; FARKUS received it 45 seconds late, completing extra laps locally during the queue drain).
- **Client-side position lerp (25%/frame) for remote players** — replaced instant-snap consumer with continuous lerp toward server's last-reported position. Big-jump exception (>200 fxp delta) still snaps directly for actual respawn teleports. Turns visible discrete jumps into continuous motion.
- **Local physics owns `physics_speed` for remote pids** — removed the `players[op].physics_speed = rs->speed/256` override from the PLAYER_SYNC consumer. Was harmless in pre-lerp snap-only mode but capped local physics' ability to accelerate remote cars in lerp mode (`physics_accelerate_forwards` couldn't compound because every frame the override reset speed to server's stale report). Now `cpu_*` flags from INPUT_RELAY drive remote-car motion smoothly between syncs.

### Race mechanics

- **`game.players` from `opponent_count + 1`** atomic delivery in GAME_START (was sourced from `g_mnet.lobby_count` which raced with PLAYER_JOIN arrival timing — caused "P2 splitscreen background and sprites not rendering" because `create_player()` only iterated `p < game.players=1` and left `players[1]` uninitialized).
- **Auto-unstick respawn extended to NetLink local players** — `reset_to_last_checkpoint(p)` now fires after 64 frames (~2.1 sec) of no motion for any local player on a Saturn (primary + optional co-op P2). Was gated to offline 1P AI only before.
- **`physics_speed_x_adj` / `z_adj` ±30 fxp cap at collision response** — prevents runaway accumulation when holding gas into a wall. Collision frame adds `delta * 1.5` to adj; without cap, adj reached -47, -65, etc., causing the car to creep -47 units/frame off-track until off-track-respawn fired. Clamp at ±30 preserves knock-back feel; existing friction (-0.8/frame) plus the iter-3 decay (×0.5 when speed=0) quickly normalize adj after gas is released.
- **Strict-greater lap-finish check on server** — race ends when `p.lap > lap_count`, not `>=`. Spawn-line position counts as lap=1 immediately (phantom cross), so 3-real-laps = 4 line-crossings = lap=4 > lap_count=3 triggers finish.

### Local-coop on Saturn

- **`s_is_local[]` correctly identifies primary + co-op P2** — both flagged as local; remote pids correctly redirected to gamepad port 7 (unused) so KEY_PRESS reads don't bleed across.
- **`stop_sounds(p)` for all local slots on race-end transition** — fixes drift_sound (PCM_ALT_LOOP) persisting into lobby. Was only ceased by the gameplay loop's `drift = false` branch; race-end stops the gameplay loop so any active loop kept playing.

### Controls

- **3D Control Pad remap** — LT = brake (was B), A = use item (was L), RT = gas. Standard digital pad unchanged.
- **Y button (camera zoom) fixed** — removed the cross-slot copy at `main.c:6008` that silently reverted every non-pid-0 player's Y press to whatever pid 0 was holding.

### Diagnostics for next-test data

Every diagnostic stream in the 0.8.0 binary writes to the server's per-client log file (`/home/gary/mmm_client.log` on the gcloud VM):

- `VER=0.8` stamp at PHASE_E COMPLETE — definitively know which binary ran in any session.
- `DIAG_CARS p0=N p1=N p2=N p3=N (sel) N N N N (lobby)` at PHASE_E — confirms per-pid car selections lined up end-to-end.
- `DIAG_P1 HB f=N lap=N cp=N x=N z=N spd=N` every 60 frames — local primary state.
- `DIAG_P2 HB ...` mirror for local-coop P2 when active.
- `DIAG_SYNC pid=N rx_i=N rx_s=N age=N x=N z=N` per-pid sync receive counters every 60 frames.
- `DIAG_SYNC_STALE pid=N age=N ENTER/EXIT` edge-triggered when any remote pid's sync goes >30 frames stale (~500 ms).
- `DIAG_TRIG p=N tr=N cp=N nx=N x=N z=N` + sub-events `LAP+`, `ADV cp=N->N`, `RESET (out-of-seq)` — every checkpoint trigger for local pids, deduped to edge events only.
- `DIAG_STUCK p=N nodelta? dx=N dz=N adj=N,N` when stuck-eligible AND speed=0 AND coords are creeping; `DIAG_STUCK p=N FIRED -> respawn` on every successful auto-unstick.
- `DIAG_PUP p=N ENTER pup=N active=N shoot=N x=N z=N` and `EXIT pup=N` bracketing every powerup activation — log shows ENTER without EXIT = the powerup case hung.
- `DIAG_CAR send pid=N car=N` (every car-select send) + `DIAG_CAR lobby slot=N pid=N car=N->N` (every LOBBY_STATE car update) for end-to-end car-selection trace.

## What's NOT yet hardware-verified in 0.8.0

The iter-4 fix for "3+ player remote players appear frozen to peers" — removing the `physics_speed` override in the PLAYER_SYNC consumer — is **theory-correct based on code trace** but has not been tested on real hardware. If the symptom persists, `DIAG_SYNC_STALE` ENTER/EXIT logs will pinpoint whether it's still a sync-drop issue (bandwidth/queue) or something else, and 0.8.1 ships the data-driven fix.

## Known bugs deferred to 0.8.x

Each has diagnostics in 0.8.0 ready to capture the data needed for a code-traced fix:

- **P2 lap counter never increments in local-coop** — `DIAG_TRIG p=<P2_pid>` events on next session will show whether P2 hits the `RESET (out-of-seq)` branch on every trigger (which would mean P2's checkpoint progression isn't tracking). 0.8.1 candidate.
- **Powerup pickup inconsistent** — `DIAG_PUP ENTER`/`EXIT` brackets show whether activation completes cleanly. Also the user-reported "game froze ~halfway through a lap after activating a powerup" — if EXIT never logs, that pinpoints the specific powerup case that hangs. 0.8.2 candidate.
- **Sprite-allocator "rainbow static" on some maps** — pre-existing `DIAG_BR` log captures sprite IDs and dimensions at MAP_TILESET base. Need a clean session with all Saturns confirmed on 0.8.0 binary (via `VER=0.8` stamp) to know if the 0.6.6 fix still holds in all races. 0.8.3 candidate.
- **3+ player remote-freeze residual** (if any after the iter-4 fix) — `DIAG_SYNC_STALE` data will tell us. 0.8.x candidate if needed.

## Test plan

ONE 3-human race confirms everything:

1. Cold boot the 0.8.0 ISO. Verify `VER=0.8` stamp in server log right after PHASE_E COMPLETE.
2. Each player cycles through cars in the lobby. Verify `DIAG_CAR send` / `DIAG_CAR lobby` log entries appear, then `DIAG_CARS` at race-start shows distinct per-pid `(sel)` and `(lobby)` values matching what was picked.
3. Drive a full 3 laps. Remote players should appear smoothly moving on each peer's screen (no apparent freezing for "one or all opponents").
4. Race ends after 3 real laps. RACE_FINISH should reach all clients within a frame of server-side detection (no minutes-long delay).
5. Return to lobby. **No tire-screech / drift sound persisting.**
6. If a player gets wedged → auto-respawn after ~2 sec with pickup-sound ping.
7. If anyone reports a powerup-related freeze, capture the `DIAG_PUP ENTER pup=N` line that has no matching `EXIT`.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
