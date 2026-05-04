# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.7.2)

Mid-test diagnostic release. ONE confirmed fix (the car-selection bug, 100% confidence) plus comprehensive instrumentation so the next test session captures everything needed to fix the remaining bugs in 0.7.3 with the same code-traced certainty.

The 5/4 daytime session log on the server (FARKUS playing, single Saturn, local-coop P2 active) revealed two things this build addresses:

1. **No way to know which binary version was running** — without that, we can't tell whether the 0.7.1 stuck-respawn fix actually fired in any specific session.
2. **No P2-side state logging** — the previous builds had a per-frame `DIAG_P1 HB` for the host primary but nothing equivalent for the local-coop P2 slot, so we had zero data on P2's actual lap/checkpoint progression.

Both are now instrumented.

## Bug fixed (100% confidence)

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
- `DIAG_TRIG p=N ADV cp=N` — checkpoint advance (in-sequence)
- `DIAG_TRIG p=N RESET tr=N cp=N nx=N (out-of-seq)` — out-of-sequence trigger hit, `reset_to_last_checkpoint(p)` fired (this is the teleport mechanism the user reported for P2)

If P2's trigger events show `RESET` repeatedly while P1's show `ADV`/`LAP+`, that pinpoints the bug exactly: P2's checkpoint progression isn't tracking the same sequence P1's is, so every checkpoint cross is treated as out-of-order and reset is triggered.

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
