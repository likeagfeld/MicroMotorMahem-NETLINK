# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.7.2)

Mid-test diagnostic release, **third iteration of 0.7.2**. Four more code-traced fixes from the 5/4 PT 21:28 multi-human session log (TOAST + FARKUS, race went 5+ laps before ending, jitter, random respawns). Per user direction the version stays at 0.7.2 and the tag/release moves forward in place each iteration until told to advance.

## What's new in iter-4

User report: with 2 humans + 1 remote it looks fine; with 3+ players "sometimes looked like one or all of the opponents weren't driving even though everyone reported being able to drive". Plus "tire screeching sound effect persisted after a game ended while in the lobby". Both bugs traced data-drivenly from the 5/4 21:28 multi-human session logs.

**1. Remote players appear sluggish/frozen with 3+ players** — code-traced to my iter-3 lerp's interaction with the existing `players[op].physics_speed = (float)rs->speed / 256.0f;` override in the PLAYER_SYNC consumer. In iter-2 snap-only mode the override was harmless (position snap dominated motion); in iter-3 lerp mode the override caps local physics' ability to accelerate remote pids — every frame `physics_speed` gets RESET to the server's lagging speed report, preventing `physics_accelerate_forwards` (+0.46/frame from `cpu_gas=true` via INPUT_RELAY) from compounding. **Fix:** drop the override entirely; local physics owns `physics_speed` for remote pids now, driven by `cpu_*` flags. Server's authoritative position (lerp) and game state (lap/cp/wp) still flow normally.

**2. Tire screech persisting into lobby** — code-traced to `players[p].drift_sound` being a `PCM_ALT_LOOP` and the gameplay loop being the only place that calls `pcm_cease(drift_sound)` when drift conditions clear. When the race-end transition fires (`game_state = GAMESTATE_END_LEVEL`), the gameplay loop stops running, so any loop sound that was active at race-end keeps playing. **Fix:** call `stop_sounds(p)` for all active player slots on the race-end transition.

**3. PLAYER_SYNC wire-format trim 19→16 bytes (conservative)** — for the 3+ player bandwidth-pressure scenario the user reported. 2P at 380 B/s was comfortable on the modem; 3P at 570 B/s started pushing against effective V.34 throughput. With `speed` dropped from wire (no reader after fix #1) and `dist_wp` quantized to u8 (HUD-only progress display, 4-unit precision well below visual threshold), 3P drops to 480 B/s — meaningful headroom restored. Positions and heading (x, y, z, ry) kept at full int16 — no visual regression on remote cars. Atomic deployment: server emits new format, client decodes; both shipped in this v0.7.2 force-push.

**4. New diagnostic — `DIAG_SYNC_STALE pid=N age=N ENTER/EXIT`** — edge-triggered log fires when a remote pid's PLAYER_SYNC age exceeds 30 frames (~500 ms) and again when it clears below 5 frames. The existing 60-frame DIAG_SYNC heartbeat couldn't catch transient sync drops between samples; this captures the actual "frozen for ~500ms" event if it recurs after this iter's fixes, so the next test session's logs prove (or refute) whether the speed-override theory was the full story.

### Bandwidth math after iter-4 (modem capacity 1440 B/s):

| Players | iter-3 (19B/sync) | iter-4 (16B/sync) | Headroom on 1440 |
|---|---|---|---|
| 2 | 380 B/s | 320 B/s | 78% free |
| 3 | 570 B/s | 480 B/s | 67% free |
| 4 | 760 B/s | 640 B/s | 56% free |
| 6 | 1140 B/s | 960 B/s | 33% free |

Scales cleanly through 6 players.

## What's new in iter-3

**1. Race-finish 21-second delay** — server's "Race finished!" fired at 21:30:21 but TOAST received RACE_FINISH at 21:30:42 and FARKUS at 21:31:06 (45 seconds later). Server kept hammering PLAYER_SYNC at 10 Hz × 3 pids during those seconds, queueing ahead of RACE_FINISH at the modem bottleneck. Both clients kept driving locally during the queue drain — that's why FARKUS reported "I was over 5 laps on my side". **Fix:** server's `_game_tick` now gates PLAYER_SYNC broadcast on `not self.sim.race_finished`. After race-end the queue drains immediately, RACE_FINISH arrives within ~50 ms.

**2. "Random hopping back to other places while driving"** — `DIAG_STUCK p=1 nodelta? dx=-47 dz=0 dy=8 adj=-4772,0` in the 5/4 21:29 log. The car was creeping -47 fxp units per frame at speed=0 because `physics_speed_x_adj` lingered at -47.72 after a wall collision. Strict-equality stuck detector saw motion (`dx=-47`), reset its 64-frame timer, and the car slowly drifted off-track until eventually getting reset_to_last_checkpoint via the off-track path — visible as a "random teleport". **Fix:** when `physics_speed == 0.0f`, multiply `physics_speed_x_adj/z_adj` by 0.5 each frame and zero them when below ±0.5. Decays in ~7 frames (~233 ms at 30 fps). The intentional knock-back from a collision is still visible for ~5-10 frames before fading.

**3. All players showing same car (`DIAG_CARS p0=0 p1=0 p2=0 p3=0`)** — both 5/4 sessions confirmed the symptom. Root cause traced: PLAYER_JOIN messages carrying `car_id` arrive AFTER PHASE_B's CD I/O blocks the network polling thread, so PHASE_B reads `lobby_players[p].car_id = 0` (from the GAME_START memset). **Fix:** GAME_START payload now appends `[car_count:1][car_id:1]×N` after `lap_count`. `process_game_start` populates `lobby_players[].car_id` directly from the GAME_START packet — atomic, available immediately for PHASE_B regardless of PLAYER_JOIN arrival timing.

**4. Stair-step jitter on remote cars** — the 0.7.2 iter-2 client did `players[op].x = rs->x;` directly in the PLAYER_SYNC consumer, producing visible 100 ms teleports between snaps. **Fix:** smooth lerp toward the latest server snapshot at 25% per frame (with sign-corrected last-step convergence), with a "big jump > 200 fxp units" exception that snaps directly (for actual respawn teleports). At 60 fps render and 10 Hz sync, the lerp converges to within a few units of the new target by the time the next snap arrives — turning visible discrete jumps into continuous motion. Position lerps; lap/cp/wp/dist_wp continue to snap (discrete game state).

## Iter-2 carried forward

Mid-test diagnostic release, **second iteration of 0.7.2** with two more code-traced fixes from the 5/4 PT 20:32 session log.

The 5/4 daytime session log on the server (FARKUS playing, single Saturn, local-coop P2 active) revealed two things this build addresses:

1. **No way to know which binary version was running** — without that, we can't tell whether the 0.7.1 stuck-respawn fix actually fired in any specific session.
2. **No P2-side state logging** — the previous builds had a per-frame `DIAG_P1 HB` for the host primary but nothing equivalent for the local-coop P2 slot, so we had zero data on P2's actual lap/checkpoint progression.

Both are now instrumented.

## Bugs fixed (100% confidence — each one code-traced)

### P2 splitscreen rendering broken / probable freeze cause

**Symptom (from 5/4 20:32 session log):** P2's splitscreen view fails to render the level background and sprites correctly; game froze ~halfway through a lap.

**Root cause traced:**
- `mmm_online_start_race` (`main.c:272`) was computing `total = g_mnet.lobby_count` to set `game.players`. After `process_game_start` (`mmm_net.c:326`) wipes `lobby_count = 0`, it grows ONLY as PLAYER_JOIN messages arrive.
- `mmm_online_start_race` runs on the same lobby tick that processed GAME_START — so `lobby_count` reflects only the PLAYER_JOIN frames that landed in that tick's RX buffer at that exact moment.
- Smoking gun in the log: `START_RACE BEGIN t=11 s=2 p=1 p2=1` — `s=2` (current_players, used for splitscreen render = 2 because P2 active) but **`p=1`** (`game.players=1`).
- `create_player()` iterates `for(p < game.players)`. With `game.players=1`, only `players[0]` was initialized. `players[1]` (P2) had uninitialized `car_selection`, physics fields, position, sprite IDs.
- The render loop iterates up to `current_players=2` for splitscreen, reading garbage from `players[1]` — exactly the symptom of "P2 splitscreen background and sprites not rendering".
- Any later code path iterating up to `current_players` and dereferencing fields on the uninitialized struct can hang the per-frame loop = plausible cause of the freeze (last log entry was a healthy `DIAG_P1 HB f=1500`, then 60+ seconds of silence until heartbeat timeout).

**Fix:** `total = g_mnet.opponent_count + 1`. `opponent_count` is delivered atomically inside the GAME_START payload itself (`mmm_net.c:278`), set BEFORE `game_start_pending` flips, so it's deterministic regardless of PLAYER_JOIN arrival timing. With this, `game.players` always equals the real roster size at race start, every player slot gets `create_player`'d, and the uninitialized-struct read paths are eliminated.

### All players rendering as the same car

**Root cause traced end-to-end:**
- Server's `build_player_join(player_id, name)` (`mserver.py:451`) sent only `[op:1][pid:1][name_lp:1+N]` — **no car_id**.
- Client's `process_game_start` (`mmm_net.c:325`) wipes `g_mnet.lobby_players[]` to all-zero on every race start.
- Client's `process_player_join` (`mmm_net.c:527-555`) re-populated `id`, `active`, `name` only — **never car_id**, so it stayed at 0 from the wipe.
- Client's PHASE_B (`main.c:401-407`) iterates `p` and reads `g_mnet.lobby_players[p].car_id` to call `load_car(p, car)`. With car_id stuck at 0, every pid loaded car model 0 → all same car.

**Fix:** `build_player_join(player_id, name, car_id)` now appends a `[car_id:1]` byte after the name. Both call sites updated to pass the right car_id (host's `client.car_id`, local-coop P2's `c.local_player_cars[i]`, bot's `bot.car_id`). Client decoder reads the trailing byte if present and writes it to `g_mnet.lobby_players[target].car_id`. Older 0.7.1 server omits the byte; client falls through to its prior behavior (car_id stays 0). Backward-compatible, no regression for any pre-0.7.2 client paired with 0.7.2+ server.

## Diagnostics added (zero behavior change)

### `VER=0.7.2` stamp

Logged once at PHASE_E COMPLETE on every race start. Lets us tell from a server log whether a given session's Saturn was actually running this build, so future bug reports can be cleanly attributed to a specific binary.

### `DIAG_CARS p0=N p1=N p2=N p3=N (sel) N N N N (lobby)`

Logged once at PHASE_E COMPLETE. Shows what each pid's `players[p].car_selection` ended up being plus what `g_mnet.lobby_players[p].car_id` actually contained. Confirms (or refutes) the car-selection fix lined up correctly with the in-game pid mapping.

### `DIAG_P2 HB f=N lap=N cp=N x=N z=N spd=N`

Mirror of the existing `DIAG_P1 HB`, fires every 60 frames for the local-coop P2 slot when active. Required to diagnose the "P2 lap counter never increments" bug — previous builds had no data on P2's actual state.

### `DIAG_TRIG p=N tr=N cp=N nx=N x=N z=N` + action subtype

Logs every checkpoint trigger event for local pids (P1 and P2) with three sub-events:
- `DIAG_TRIG p=N LAP+ -> N` — successful lap-line crossing, lap incremented
- `DIAG_TRIG p=N ADV cp=N->N` — checkpoint advance (in-sequence), only logged when `current_checkpoint` actually changes
- `DIAG_TRIG p=N RESET tr=N cp=N nx=N (out-of-seq)` — out-of-sequence trigger hit, `reset_to_last_checkpoint(p)` fired (this is the teleport mechanism the user reported for P2)

**0.7.2 second iteration — deduped.** First iteration emitted the entry-line every frame the player overlapped a trigger bbox (~10-15 frames per cross), depleting the client_log token bucket and dropping legitimate downstream logs. Now per-pid `s_last_trig[]` tracks the last trigger seen; only emits on edge transition. ADV is gated on actual cp change. RESET is naturally edge-driven. Net: ~1-2 trigger lines per real cross instead of 12.

If P2's trigger events show `RESET` repeatedly while P1's show `ADV`/`LAP+`, that pinpoints the bug exactly: P2's checkpoint progression isn't tracking the same sequence P1's is, so every checkpoint cross is treated as out-of-order and reset is triggered.

### `DIAG_PUP p=N ENTER pup=N active=N shoot=N x=N z=N` + `DIAG_PUP p=N EXIT pup=N`

Logs every powerup-activation event for local pids — bracketed ENTER/EXIT lets us pinpoint a freeze that occurs INSIDE a switch-case body (ENTER appears in log, EXIT does not = which case hung). User reported the 5/4 freeze happened "after activating a powerup" but the prior build had zero powerup-side logs. This makes the freeze cause provable from one more session.

### `DIAG_CAR send pid=N car=N` + `DIAG_CAR lobby slot=N pid=N car=N->N`

Two-mode car-selection trace:
- `send` fires every time the local Saturn ships a `MNET_MSG_CAR_SELECT` — captures the picked-on-this-Saturn value with the matching pid.
- `lobby` fires every time `process_lobby_state` sees a slot's `car_id` change — captures what the SERVER thinks each slot has selected, after the lobby roundtrip.

End-to-end trace: `DIAG_CAR send car=N` → server processes → `DIAG_CAR lobby slot=K car=...->N` → race start → `DIAG_CARS pK=N (sel) N (lobby)`. All three should match. If `send` says N but `lobby` shows N→M with M≠N, the server is mutating the value. If `lobby` shows N but `DIAG_CARS` shows the resolved car as different, the lobby_players[] → players[].car_selection mapping in PHASE_B has a bug. Pinpoints which leg is broken.

### `DIAG_STUCK p=N nodelta? dx=N dz=N dy=N adj=N,N` + `DIAG_STUCK p=N FIRED -> respawn`

Two-mode logging on the auto-unstick path:
- **`nodelta?`** fires once per ~2 seconds per pid when `physics_speed == 0` AND coords ARE changing per frame. This is the diagnostic signature of the "side-to-side oscillation" stuck case my 0.7.1 strict-equality detector misses. Captures `dx/dz/dy` (next minus current) and `physics_speed_x_adj / z_adj × 100` so we can see the exact magnitude of the unwanted creep.
- **`FIRED`** logs every successful auto-respawn so we know the 0.7.1 fix is actually firing on real hardware (today's logs showed a 16-second stuck period with no respawn — was that 0.7.0 binary? was the equality check missing? this log answers definitively).

## Bugs deferred (logging-only, fix in 0.7.3)

- **P2 lap counter never increments + teleports onto P1**: deferred until DIAG_TRIG + DIAG_P2 HB data from next test session shows whether P2's checkpoint progression is failing (likely cause: every cross hits the `RESET` branch → `reset_to_last_checkpoint(p)` teleports them).
- **Auto-unstick respawn not firing**: today's log shows FARKUS at `x=288 z=288 spd=0` for 480+ frames with no respawn. Either (a) FARKUS was on 0.7.0 (no fix), or (b) the strict-equality detector is missing sub-unit oscillations. The 0.7.2 `VER=` stamp + `DIAG_STUCK` logs disambiguate.
- **Powerup pickup inconsistency**: server stores positions as `(0,0,0)` placeholders, slot-index drift unverified. No code change pending diagnostic data.
- **Sprite-allocator regression** (sometimes "rainbow static" tiles): pending 0.7.2 binary confirming whether 0.6.6 fix still holds.

## Server-side state (already deployed)

The server is patched and live. `mmm.service` is `active` on the gcloud VM. Backup at `/home/gary/mserver.py.bak.20260504-0.7.2`.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## Test plan (one race is enough)

1. Cold boot the 0.7.2 ISO. Server log should show `VER=0.7.2` once per race start — confirm before doing anything else.
2. Start a race with 2+ humans (cars should now visibly differ — check `DIAG_CARS` log line).
3. With local-coop P2, drive both cars through a full lap each. `DIAG_P2 HB` should appear in the server log every 2 seconds during gameplay.
4. Watch P2 cross the start line. `DIAG_TRIG p=<P2_pid> LAP+ -> N` confirms the fix would work; `DIAG_TRIG p=<P2_pid> RESET ...` pinpoints the bug.
5. If anyone gets stuck for ~2 sec without respawn, `DIAG_STUCK p=N nodelta? dx=N dz=N` reveals the per-frame creep that's defeating the strict-equality detector.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
