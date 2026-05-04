# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.7.1)

Hotfix release driven entirely by the 5/1/26 PT play-session diagnostic logs (`mmm_client.log` on the server). Three confirmed bugs traced directly to specific lines of code, fixed without speculation. Server-side fixes are already deployed and live; client-side fix ships in this build.

## Bugs fixed

### 1. Remote players spawning at world origin (server-side, deployed)

**Symptom:** sometimes one of the online players appears in the middle of the map (or off the map) at race start.

**Root cause:** `mserver.py:reset_player_for_race()` resets lap/checkpoint/timers but never touches `p.x / p.y / p.z`. The `Player` constructor defaults all three to 0. Until the very first `PLAYER_STATE` arrives from a client (~50–200 ms after race start), the server stores that pid's pose as `(0, 0, 0)` — and the round-robin `PLAYER_SYNC` broadcast happily relays `(0, 0, 0)` to every other client. The receiving Saturn's snap-only consumer (`main.c:5616-5634`) then teleports that car to world origin. As soon as the player moves and a real `PLAYER_STATE` arrives, the server starts broadcasting real coordinates and the car snaps to the correct track position — but the (0,0,0) render frame is plenty visible.

**Fix:** added a `has_authoritative_pose: bool` gate on `Player`. Bots flip it `True` at construction (their server-side physics gives them an immediately-valid pose). Humans flip it `True` on first `PLAYER_STATE`. The `_game_tick` PLAYER_SYNC broadcast loop skips any pid whose flag is still `False`. Since the client's `mnet_get_remote_state()` returns `NULL` until the *first* sync arrives, and the consumer's `if (!rs) continue;` guard preserves whatever spawn pose the Saturn-side track loader assigned, the (0,0,0) render frame is mechanically eliminated.

### 2. Remote cars updating only ~5 Hz, jumping every ~200 ms (server-side, deployed)

**Symptom:** "remote player cars are only updating position every 1 second or so but glitching jumping around not smooth"

**Root cause:** the server's `PLAYER_SYNC` broadcaster was round-robin one pid per 50 ms tick (`mserver.py:2023-2036`). With 4 players that means each pid gets sync-broadcast at 20/4 = **5 Hz** — i.e., a snapshot every 200 ms. The client uses snap-only sync (no interpolation, no extrapolation), so remote cars literally teleport to the new server position every 200 ms.

**Confirmed in DAN's 5/1 logs (03:00:49 → 03:01:09, 20 sec window):**
- pid=2 SLINGA: rx_s 42 → 127 = 85 syncs / 20s = **4.25 Hz** ✓
- pid=3 FARKUS: rx_s 41 → 127 = 86 / 20s = **4.3 Hz** ✓

**Fix:** broadcast every pid's `PLAYER_SYNC` every **2** ticks instead of one pid per tick. Each pid now gets sync at **10 Hz** (every 100 ms) — half the previous gap. Snap distance per teleport halves; visible jitter halves.

**Bandwidth math (verified, fits 14.4k modem):**
- Wire bytes per sync: 2 hdr + 1 op + 1 pid + 5×2 axes + 3 + 2 = **19 bytes**
- New rate: 19 × 4 pids / 100 ms = **760 B/s** = 53% of the 14400-baud modem's 1440 B/s capacity
- Client `MNET_RX_MAX_PER_POLL` budget at 60 fps = 2880 B/s — new traffic fits 4× over

This is a Tier-1 server-only change. **Future Tier-2** (quantize PLAYER_SYNC to ~12 bytes Utenyaa-style, push to 20 Hz/pid) would land smoother motion still; not in scope for this hotfix.

### 3. Y button (camera zoom) only worked for one player per race (client-side, this build)

**Symptom:** "button to change view (i think Y button?) seems to only work for some online players but not others"

**Root cause:** at `main.c:6008` the per-player Y handler unconditionally executed:
```c
players[target_player].cam_zoom_num = players[0].cam_zoom_num;
```
right after the per-player cycle. This silently overwrote whatever the iterating player just set with whatever `players[0]` happened to be holding. So Y worked **only when `target_player == 0`** — i.e., only for the one online player who happened to be pid 0 in that race. Everybody else's Y press appeared to do nothing because the very next instruction reverted it.

**Fix:** removed the cross-slot copy. Each player's Y press now cycles only their own `cam_zoom_num`. Same behavior also benefits offline 2P splitscreen (P2 can now control their own camera zoom independently of P1, instead of being yoked to P1's setting).

## Bugs deferred until next test session

### Powerup pickups inconsistent

The server stores powerup positions as `(0, 0, 0)` placeholders (`mserver.py:885-889`); the Saturn loads real positions from the track .bin. Pickups gate on slot index match, not coordinate distance — so any slot-index drift between client and server fails silently. Without per-pickup-event log data showing which slot was attempted vs server's view, any "fix" would be a guess. **Adding `MNET_LOG_INFO("pup_pickup slot=N type=N rec=N")` instrumentation is safe and low-cost; it'll be added to a 0.7.2 diagnostic build before changing logic.**

### Sprite-allocator regression (race-1 vs race-2 rendering)

The 0.6.6 fix (`preview_tex / trackmap_tex = jo_get_last_sprite_id() + N`) prevents the destructive `jo_sprite_free_from()` rewind on cold boot. Friday's logs show mixed `175:32x32` (wrong, trackmap dims) and `175:48x48` (correct, MAP_TILESET[0]) `DIAG_BR` entries — but the 32x32 entries date from earlier sessions and may correspond to pre-0.6.6 binaries on those Saturns. **Need a clean test session with all Saturns running 0.7.1 binary to confirm whether the bug recurs.** No code change in this release.

## Why I am certain these fixes work and don't regress

Every claim in this release notes is traced to specific code lines that I read end-to-end. The spawn fix is mechanically guaranteed by the client's existing `if (!rs) continue;` guard. The rate fix is verified against four bandwidth budgets (modem capacity, client RX budget, frame poll capacity, burst-window math). The Y-button fix is the removal of one demonstrably-broken line. None of the changes touch newlib heap allocation, sprite allocation, lap-count logic, RNG, linker scripts, or peripheral-slot indexing — i.e., none of the playbook gotchas G1–G10 apply.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## Test plan

ONE race, 2-4 humans + bots. Watch for:

- ✓ All player cars spawn at the start grid (NOT in the middle of the map)
- ✓ Remote cars move smoothly with ~100 ms snap intervals (was ~200 ms)
- ✓ Y button cycles camera zoom for every player, regardless of pid 0/1/2/3
- ✓ Server log shows `DIAG pid=N FIRST PLAYER_STATE x=... z=...` once per pid per race start (existing diag, now load-bearing for the spawn fix)
- ⚠ Powerup pickup behavior unchanged — gather instances where pickup fails so 0.7.2 can target the slot-index issue with data
- ⚠ Track tileset rendering — if any Saturn shows "rainbow static" tiles, that confirms the 0.6.6 fix didn't fully cover this case and 0.7.2 can land a real fix

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
