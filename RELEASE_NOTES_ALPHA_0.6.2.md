# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.2)

Hotfix on 0.6.1. Adds two things — a real fix for the "race ends after 2 laps" off-by-one, and ground-truth instrumentation so the user's next test definitively confirms whether the 0.6.1 rendering fixes worked or not (no need for another test cycle to diagnose if they didn't).

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title screen → **Online Play** → enter name → dial **199406**. A or C to ready, START to request race.

## Fixes shipped over 0.6.1

| Bug | Root cause | Fix |
|---|---|---|
| **Race ends after completing 2nd lap, not 3rd** (lap_count=3 default) | Server checked `p.lap >= self.lap_count` — strict-equal would end the race when p.lap reaches 3. But the spawn position is AT `checkpoints[0]` (the start/finish line itself), so the very first START crossing right after the 6-second countdown counts as `p.lap=1` even though the player hasn't actually driven a lap yet. With `>=`, only 2 real laps need to be driven before the race ends (1 spawn + 2 real = 3 crossings). Upstream MMM offline (`main.c:1450`) uses strict `>` (`if(players[p].laps > MAX_LAPS)`) which requires 4 crossings = 3 real laps, matching the user's expectation. | Two server-side checks (`handle_player_state` implicit progression and `handle_lap_complete`) now use `p.lap > self.lap_count` to match upstream. With `lap_count=3`: race ends after 4 START crossings = 3 real laps. |
| **Need diagnostics to confirm 0.6.1 rendering fixes worked** | 0.6.1 shipped three defensive cleanups but I have only ~30% confidence each was the actual root cause. Without instrumentation, another test cycle is needed if any race still looks broken. | Added `DIAG_RACE` (race counter, track_id, model_total, total_sections, total_waypoints) and `DIAG_XP` (first 4 `xpdata_` pointers + first dword at xp[0] and xp[1]) per race at PHASE_E. Lets us diff race 1 vs race 2 against ground truth: if race 2's `total_sections` matches the new track but rendering is still broken, it's not stale `xpdata_`. If pointers haven't moved between races, the load_level didn't actually run. |

## What's IN alpha 0.6.2

- All 0.6.1 functionality (model-data zero-clear, RBG0 disable cycle, display-init dedup)
- **Race ends after the correct number of laps** (3 real laps for default lap_count=3)
- Ground-truth instrumentation so we converge in one test cycle

## Test plan

One race that goes the distance:
1. Cold boot → enter online → start race → drive at least 4 START crossings (= 3 real laps)
2. Confirm: race ends with `Race finished!` on the 3rd real lap
3. Watch for any rendering corruption (3D background, track geometry, sprite glitches)
4. Back to lobby → second race → drive again
5. Watch for rendering corruption in race 2

Whether things work or break, the diagnostic logs (`DIAG_RACE`, `DIAG_XP`, `DIAG_P1 INIT/HB/LAP/CP`, `DIAG_PER`, `DIAG_GP`, `DIAG_REPIN`) capture enough state for us to know exactly what's happening without needing to test again.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
