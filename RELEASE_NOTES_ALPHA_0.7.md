# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.7)

Consolidation release rolling up every fix from the 0.6.x line into a single tested cut, plus a 3D Control Pad remap and a fresh batch of network-sync diagnostics that survive into gameplay so we can fine-tune online sync from a single test session of data.

Massive thanks to [jberetta](https://github.com/jberetta82) for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## What's new in 0.7

### 3D Control Pad remap (per user spec)

The 3D Control Pad now drives the cars closer to a real arcade racing layout:

| Control | Standard digital pad | 3D Control Pad |
|---|---|---|
| Steer | D-pad ← / → | Analog stick X (~32-unit deadzone) + D-pad fallback |
| Gas | B button | **R trigger** |
| Brake | A button | **L trigger** *(was B button)* |
| Use item | L button | **A button** *(was L button)* |
| Horn | C button | C button |
| Start / pause | Start | Start |

Standard digital pad is unchanged. Local co-op P2 follows the same per-pad rule.

### Online sync diagnostics retained

The 0.6.x rendering-bug diagnostics have been stripped (root cause shipped in 0.6.6). Replacing them is a permanent online-sync diagnostic set so we can quantify drift and fine-tune the passthrough sync model from one test session:

- `DIAG_P1 INIT / LAP / CP / HB` — local player 1 transitions and 60-frame heartbeat (lap, checkpoint, x, z, speed). Unchanged.
- `DIAG_P2 port=N bits=XX data=XXXX id=XX` — local-coop P2 input transitions whenever the bits set changes. Unchanged.
- `DIAG_RACE n=N t=N models=N sect=N wp=N` — race-init snapshot once per race start. Unchanged.
- `DIAG_SYNC pid=N rx_i=N rx_s=N age=N x=N z=N` — **NEW**, fires every 60 frames per remote pid:
  - `rx_i` — total `INPUT_RELAY` packets received for this pid since race start
  - `rx_s` — total `PLAYER_SYNC` packets received for this pid since race start
  - `age` — local frames since the last `PLAYER_SYNC` for this pid (freshness)
  - `x`, `z` — last server-asserted position for this pid

These four metrics are exactly what's needed to fine-tune sync — receive rate per stream, plus per-pid freshness and last position. One race tells us whether `PLAYER_SYNC` is keeping up with the 60 fps client loop, whether `INPUT_RELAY` is bursting or starving any pid, and whether any remote pid is going stale.

### Online leaderboard reset

The persistent leaderboard JSON has been wiped on the server. All player records start fresh at 0 wins / 0 podiums / 0 races / 0 points. This is a clean baseline for the 0.7 line — your `MMM_NAME` slot is preserved on each Saturn, but server-side stats are zero.

## What was already fixed in the 0.6.x line (and is in 0.7)

For anyone jumping straight to 0.7 from 0.5 or earlier, here's what 0.6.x cumulatively delivered:

- **Cold-boot black screen** — caused by binary size crossing a ~317 KB threshold. Fixed by switching to `-Os`. (0.4 → 0.5)
- **Player 2 controls in local co-op online** — `Smpc_Peripheral` slot mismatch with `jo_inputs`. Fixed by reading the correct raw SBL slot for the resolved P2 port. (0.5 → 0.6)
- **Race never ending after final lap** — the old offline `PLAYER_STATE` handler was inside `cpu_control()` which never runs in online mode. Lap-counter check moved to the gameplay path that fires for online. Server check upgraded from `>=` to `>` so the spawn-line crossing isn't counted as lap 1. (0.6 → 0.6.1)
- **Lap counter cropped at HUD bottom** — repositioned. (0.6.1)
- **Lobby leftover artifacts** — re-enable `jo_disable_background_3d_plane` on race-end. (0.6.2)
- **Track tileset rendered as rainbow static** — *the* big one, fixed in 0.6.6. Two bootstrap lines I'd added in 0.6.0's `PHASE_C` of `mmm_online_start_race` (`preview_tex = game.map_sprite_id; trackmap_tex = game.map_sprite_id;`) caused `load_preview()` and `load_trackmap()` to free every sprite from id 175 onward, wiping the freshly-loaded 44-tile track tileset on every race. Fix: remove those two lines, replace with `preview_tex = jo_get_last_sprite_id() + 1; trackmap_tex = jo_get_last_sprite_id() + 2;` so the `jo_sprite_free_from()` calls inside the upstream loaders early-exit instead of running the destructive free path. (0.6.6)

## How the server ranks 2nd / 3rd / 4th place

Common question: when the first player crosses the finish line on their final lap, how does the server score the other racers?

Every client streams `PLAYER_STATE` continuously with their `lap`, `current_checkpoint`, `current_waypoint`, and `dist_to_next_waypoint`. The server stores the latest values per pid. Every 5 ticks (4 Hz) `recompute_positions()` ranks every player by the tuple `(lap, current_checkpoint, -dist_to_next_waypoint)` — most laps wins, ties broken by furthest checkpoint, then shortest distance to next waypoint. Finished players sort by `finish_time` ascending, DNF / disconnected sort last.

When 1st place crosses the line on their final lap, the server snapshots positions one final time and broadcasts a single `RACE_FINISH` payload with every pid's position 1–N and total time. 1st place gets a real `finish_time`; 2nd / 3rd / 4th get the race wall-clock at finish moment since they didn't actually cross.

So 2nd / 3rd / 4th are determined by **who was furthest along the track at the instant 1st crossed the line**, using the lap+checkpoint+waypoint-distance metric.

## Test plan

Cold boot → online → start race → drive at least one lap → race ends or disconnect.

Expected new diagnostic streams in the server log:

```
DIAG_P1 HB f=60 lap=0 cp=2 x=... z=... spd=...
DIAG_SYNC pid=1 rx_i=58 rx_s=58 age=1 x=... z=...
DIAG_SYNC pid=2 rx_i=59 rx_s=58 age=2 x=... z=...
DIAG_SYNC pid=3 rx_i=58 rx_s=57 age=3 x=... z=...
```

`rx_i` and `rx_s` should both climb at roughly the local frame rate (one packet per ~frame, give or take coalescing). `age` should stay small (single digits) — high age means the pid is going stale on the wire. `x`, `z` should track that pid's actual track position.

3D pad remap test: with a 3D Control Pad in port 1, hold L trigger → car should brake. Hold A → should fire/use whichever powerup is in hand. Hold R trigger → gas. Standard digital pad in port 1: A still brakes, L still uses item, B still gas (no behavior change).

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
