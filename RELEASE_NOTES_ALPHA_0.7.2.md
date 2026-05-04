# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.7.2)

Mid-test diagnostic release, **second iteration of 0.7.2** with two more code-traced fixes from the 5/4 PT 20:32 session log. Per user direction the version stays at 0.7.2 and the tag/release moves forward in place each iteration until told to advance.

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
