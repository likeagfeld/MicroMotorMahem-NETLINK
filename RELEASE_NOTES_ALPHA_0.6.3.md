# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.3)

Hotfix on 0.6.2. Reverts the 0.6.1/0.6.2 "defensive cleanup" attempts which made rendering WORSE in real testing. Keeps the lap-count off-by-one fix from 0.6.2 and the per-race instrumentation, and adds a focused fix for the lobby-background leftover-artifacts bug.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## Changes since 0.6.2

| Change | Why |
|---|---|
| **Reverted**: PHASE C zero-out of `xpdata_`/`pdata_LP_`/`cdata_`/`map_section[]` entries | User reported that 0.6.2 made rendering MORE broken (even in race 1) instead of better. The zero-out was nulling pointers that upstream code apparently dereferences for indices `>= model_total` — getting away with it when those entries held random garbage from un-init'd memory, but crashing or producing worse corruption when set to NULL. |
| **Reverted**: `xpdata_`/`pdata_LP_`/`cdata_` linkage from file-scope back to `static` | Promoting them to file-scope shifted BSS layout — the cascading offset of all subsequent statics may have been the actual cause of race-1 rendering corruption in 0.6.2. Putting `static` back restores the original BSS placement that the upstream HUD/PUP/sprite-id math depends on. The DIAG instrumentation now uses a helper function defined where `static xpdata_` is visible, instead of an extern reference. |
| **Reverted**: extra `jo_disable_background_3d_plane` before `init_3d_planes` | Cargo-culted theory; both `disable` and `enable` only flip a bit, neither actually clears VDP2 RBG0 VRAM. Removed. |
| **Reverted**: skip-display-init-when-current_players-unchanged optimization | Wasn't a fix, just an optimization, and may have contributed to viewport mismatch on subsequent races. Always re-init for safety. |
| **Kept**: server-side lap-count `>` instead of `>=` (from 0.6.2) | The "race ends after 2 laps not 3" bug. Already-deployed server fix. |
| **Kept**: `DIAG_RACE` + `DIAG_XP` instrumentation per-race | Still need ground-truth on what `xpdata_` looks like in race 2 vs race 1. Now via helper function so the data is captured without disrupting linkage. |
| **NEW fix**: disable RBG0 + clear NBG1 when end_level transitions to LOBBY | User reported "lobby screen rendering background is broken and shows left over artifacts from the previous race". Race-end → LOBBY path didn't reset the RBG0 plane that held the previous race's sky/floor. Now does. `mmm_online_start_race`'s `init_3d_planes` re-enables for the next race, so this is contained to the lobby's lifetime. |

## What's IN alpha 0.6.3

- All 0.6 functionality (race ends correctly, P2 controls work, leaderboard polished)
- Lap-count fix (race ends after 3 real laps, default lap_count=3)
- Instrumentation for one-shot diagnosis if anything is still off
- Lobby background no longer shows previous race's leftover sky/floor

## Test plan

One race, one return-to-lobby:

1. Cold boot → online → start race → drive at least 4 START crossings (= 3 real laps)
2. Watch for rendering corruption (3D background, track geometry, sprites)
3. Race ends with `Race finished!` (not stall) — server log confirms via `DIAG pid=0 lap N->M` lines
4. Back to lobby → confirm clean lobby background (no leftover sky/floor)
5. Start race 2 → drive briefly → confirm rendering

The diagnostic logs (`DIAG_RACE`, `DIAG_XP`, `DIAG_P1 INIT/HB/LAP/CP`, `DIAG_PER`, `DIAG_GP`, `DIAG_REPIN`) capture enough state for any remaining issue to be diagnosed without another test cycle.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
