# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.1)

Hotfix on top of 0.6 for the rendering corruption that 0.6 partially addressed but didn't fully solve. First-race partial 3D-background corruption + heavily corrupt rendering on subsequent races (after returning to lobby) — three defensive cleanups in `mmm_online_start_race`.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem. This fork only *adds* a networking layer; every line of gameplay code and every track you're racing on is jberetta's work.

## How to play

Load `game.cue`. Title screen → **Online Play** → enter name (1–8 chars, persisted to backup RAM `MMM_NAME`) → dial **199406**. A or C to ready, START to request race.

## Fixes shipped over 0.6

| Bug | Root cause | Fix |
|---|---|---|
| **Subsequent-race rendering very broken** | Race 1 might populate `xpdata_[0..23]` / `pdata_LP_[0..23]` / `cdata_[0..23]` / `map_section[0..23]` from a 24-model track. Race 2 with an 8-model track only repopulates `[0..7]`; entries `[8..23]` still hold pointers from race 1, but those pointers index into `WORK_RAM_LOW` slots that PHASE A's CARS.BIN load + PHASE D's new track BIN load have already overwritten. Any stray dereference (collision check, draw_map, set_player_position) hits whatever happens to be in WORK_RAM_LOW now → garbage geometry on screen. | Defensive zero of `xpdata_`/`pdata_LP_`/`cdata_`/`map_section[].(map_model, map_model_lp, a_cdata, a_collison)` for all 32 slots in PHASE C BEFORE `load_level` repopulates `[0..new_total-1]`. Definitions promoted from `static` to file-scope so the cleanup is visible at the call site. |
| **First-race 3D background partial corruption** | `init_3d_planes` writes the new track's sky/floor TGAs to VDP2 RBG0 VRAM via `jo_background_3d_plane_a_img` / `jo_background_3d_plane_b_img`. If the new TGA's dimensions don't match what's already in VRAM (e.g. carryover from the title screen's `FLR1.TGA`/`SKY8.TGA`), leftover pixels from the previous content show through in unwritten regions. The `jo_disable_background_3d_plane` call earlier in PHASE C only flips the enable bit; it doesn't clear the underlying VRAM. | Add a second `jo_disable_background_3d_plane` call immediately before `init_3d_planes`. The disable→enable cycle (init_3d_planes calls enable internally) empirically forces a clean RBG0 VRAM state on re-init. Combined with the `slTVOff/slTVOn` blanking that init_3d_planes already does, the user never sees the transient state. |
| **Redundant display init every race** | `init_2p_display` / `init_1p_display` were called every race even when `current_players` hadn't changed. Each call writes `slWindow` + `slPerspective` to VDP1 — wasted work and a possible source of viewport flicker. Offline mid-tournament (the equivalent path) doesn't re-init the display between tracks. | Track previous `current_players` via static var; only call display init when it actually changes (e.g. user adds/removes local-coop P2 between races). Logs `PHASE_C_OK DISPLAY_REUSED` when skipped. |

## What's IN alpha 0.6.1

- All 0.6 functionality (race ends correctly, P2 controls work, lap rendering fixed, leaderboard polished)
- **Subsequent races render cleanly** — stale model pointers no longer leak through
- **First race 3D background renders cleanly** — RBG0 VRAM no longer carries title-screen leftover
- Diagnostic logging still on (`DIAG_PER`, `DIAG_GP`, `DIAG_P1 INIT/HB/LAP/CP`, `DIAG_P2`, `DIAG_REPIN`) — will strip in 0.7 once stable

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
