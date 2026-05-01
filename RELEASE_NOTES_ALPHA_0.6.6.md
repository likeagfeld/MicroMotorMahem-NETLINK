# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.6)

**The actual fix**, derived directly from the v0.6.5 diagnostic test data — not a guess. Logs showed `DIAG_LT_POST sid=218 added=44` (track tileset loaded fine) followed by `DIAG_LP ret=175 sid=175` and `DIAG_LM ret=175 sid=175` (preview/trackmap each freed sprites 175..218 then added one back at id 175). The static `ATTRIBUTE` arrays in `objects.h` reference `MAP_TILESET+0..43` = sprite IDs 175..218 — and only 175 had data after `load_preview`/`load_trackmap` were done with it.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## The fix

Two lines I added in alpha 0.6.0 in PHASE_C of `mmm_online_start_race`:

```c
preview_tex  = game.map_sprite_id;   /* = 175 */
trackmap_tex = game.map_sprite_id;   /* = 175 */
```

I added these thinking they prevented stale-id issues. They are *exactly* what was destroying the freshly-loaded track tileset on every race.

Each subsequent `load_preview()` and `load_trackmap()` calls `jo_sprite_free_from(preview_tex)` / `jo_sprite_free_from(trackmap_tex)` — both at value 175. With `__jo_sprite_id` at 218 after `load_textures`, those `free_from(175)` calls ran the actual free path (175 ≤ 218) and rewound `__jo_sprite_id` back to 174 — wiping all 44 just-loaded track tiles. Each then added their preview/trackmap sprite at id 175 — overwriting the first track tile's VRAM. Sprites 176..218 retained their `__jo_sprite_def` metadata from the wiped track load, so polygons that referenced `MAP_TILESET+1..43` happened to render OK; polygons referencing `MAP_TILESET+0` (the most common index in the BIN-encoded polygon attributes) rendered the trackmap pixels distorted = the "rainbow static / mix of colored tiles" symptom.

**Removed those two lines from PHASE_C.** Replaced with a bootstrap right before `load_preview` / `load_trackmap`:

```c
int next_id = jo_get_last_sprite_id();   /* = 218 after load_textures */
preview_tex  = next_id + 1;              /* = 219 — past current sprite count */
trackmap_tex = next_id + 2;              /* = 220 */
```

This guarantees that the `jo_sprite_free_from(preview_tex)` call inside `load_preview` triggers the early-exit path (`sprite_id > __jo_sprite_id` → return immediately, no-op). New preview/trackmap sprites get added at IDs 219 and 220 respectively, leaving the 44 track tiles at 175..218 fully intact.

## Why this matches the offline mid-tournament path

Upstream offline mid-tournament code (`main.c:6920`) doesn't reset `preview_tex`/`trackmap_tex` between races — they retain their values from the previous race's `load_preview`/`load_trackmap` returns. After race 1 ends `preview_tex=219, trackmap_tex=220`. Race 2 PHASE_C frees from 175 → `__jo_sprite_id=174`. `load_textures` re-fills to 218. `load_preview`'s `free_from(219)` is a no-op (219 > 218), then re-adds at 219. Same for trackmap. Track tileset stays intact.

My online path skipped the offline's player_select → level_select chain (which is where `preview_tex`/`trackmap_tex` first get sane values in the offline flow), so for the *first* online race after cold boot, both globals are 0 (BSS init). Without the bootstrap fix, the first call to `jo_sprite_free_from(0)` would catastrophically free every sprite from id 0 onwards — wiping cars, players, PUP, HUD, all of it. The bootstrap above handles this case too: even if both globals come in as 0, they get re-set to 219/220 before the upstream functions touch them.

## What's IN alpha 0.6.6

- All previous fixes (race-end correct after 3 laps, P2 controls, lap-counter visibility, etc.)
- **Track tileset no longer wiped between load_textures and load_preview.** All 44 tiles correctly populated and addressable as MAP_TILESET+0..43 throughout the race.
- Diagnostics retained (`DIAG_LT`, `DIAG_LP`, `DIAG_LM`, `DIAG_3DP`, `DIAG_BR`) so the next test confirms the fix. Will strip in 0.7.

## Test plan

ONE race. Cold boot → online → start race → drive at least one lap → race ends or disconnect.

Expected diagnostic output:

```
DIAG_LT ret=175 sid=218 added=44     ← unchanged from 0.6.5
DIAG_LP ret=219 sid=219              ← was ret=175 sid=175 in 0.6.5; now 219
DIAG_LM ret=220 sid=220              ← was ret=175 sid=175 in 0.6.5; now 220
DIAG_BR 175:48x48@... 176:48x48@...  ← sprite 175 now retains MAP_Tileset[0] dims (48x48), not trackmap's 32x32
DIAG_BR 200:48x48@... 218:16x16@...  ← unchanged
```

Visually: track polygons render with correct textures. No "rainbow static" tiles.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
