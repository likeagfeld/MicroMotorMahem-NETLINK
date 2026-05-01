# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.5)

Surgical instrumentation, applied at the level of a Saturn developer who's read the actual jo_engine source code paths in question. ONE more test cycle should produce 100%-confidence diagnosis of the race-time TGA-load failure (sprite tile count `last_id=175` instead of expected 218 = only 1 of 44 tiles loaded).

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## Targeted diagnostics added in 0.6.5

Wrapped exactly the four upstream functions implicated in the v0.6.4 finding:

| Probe | Where | What it captures |
|---|---|---|
| `DIAG_LT_PRE f=NAME tiles=N sid=N` | top of `load_textures` | __jo_sprite_id BEFORE the call — anchor for the delta |
| `DIAG_LT_FREED sid=N` | after `jo_sprite_free_from` inside `load_textures` | __jo_sprite_id after the free — should be 174 if free worked |
| `DIAG_LT_POST ret=N sid=N added=N` | after `jo_sprite_add_tga_tileset` inside `load_textures` | the function's return value, the post-call __jo_sprite_id, and the delta. **`added=44`** = all tiles loaded; **`added=1`** = the bug we're chasing; **`ret=-1`** = OOM/error path triggered (which v0.6.4 said it wasn't, but we'll re-verify) |
| `DIAG_LP f=NAME ret=N sid=N` | after `jo_sprite_add_tga` inside `load_preview` | preview TGA load result + post-id |
| `DIAG_LM f=NAME ret=N sid=N` | after `jo_sprite_add_tga` inside `load_trackmap` | trackmap TGA load result |
| `DIAG_3DP sky_f=NAME rc=N data=PTR flr_f=NAME rc=N data=PTR` | end of `init_3d_planes` | return code from each `jo_tga_8bits_loader` call (0 = JO_TGA_OK) and `img.data` pointer (NON-NULL = TGA loaded into RAM, NULL = load failed silently) |
| `DIAG_3DP_MEM mem=0 spr=0 sid=N` | end of `init_3d_planes` | post-init sprite id |
| `DIAG_BR 175:WxH@A 176:WxH@A` | PHASE_E | `__jo_sprite_def[]` at sprite IDs 175 and 176 — bracket the start of the map tileset |
| `DIAG_BR 200:WxH@A 218:WxH@A` | PHASE_E | mid-set + end-set probes. If 200 still has 48x48 (cold-boot TITLE.TGA's tile dims), only the first map tile got re-loaded |
| `DIAG_USE mem=0 spr=0` | PHASE_E | currently zeros (jo_memory/sprite_usage_percent are JO_DEBUG-only and not linked in our release build) |

## What the data will show us

Cross-reference `DIAG_LT_POST.added` with `DIAG_BR 200`'s width:

- `added=44` AND `200.width=48` → all tiles loaded, the rendering bug is elsewhere (palette CRAM, ATTRIBUTE refs, VDP1 framebuffer)
- `added=1` AND `200.width=48` → confirms only sprite 175 got refreshed; 176..218 still hold cold-boot TITLE.TGA stale entries → static ATTRIBUTE arrays in objects.h dereference garbage. **This is the v0.6.4 hypothesis.** Fix path is to investigate why the loop bails after 1 iter — most likely candidate from Saturn-dev reading: jo_engine's `jo_malloc_with_behaviour` returns non-NULL but garbage when the static memory pool is full, and the TGA-tile-loop's per-iter `jo_free` (LIFO-only) doesn't actually reclaim memory after the first iter
- `added=1` AND `200.width=0` → sprite_def got partially zeroed somewhere — different bug class
- `ret=-1` from LT_POST → OOM bailout DID trigger (contradicts v0.6.4 reading); fix is to bump `JO_GLOBAL_MEMORY_SIZE_FOR_MALLOC` or reorder allocations
- `DIAG_3DP rc!=0` for sky/floor → CD I/O failing for `BG/` directory specifically; need to check ISO-mkisofs case-folding for `FLR5.tga` lowercase
- `DIAG_3DP rc=0 data=NULL` → loader returned OK but img.data is NULL — would be a jo_engine bug worth filing upstream

## Test plan — ONE CYCLE

1. Cold boot
2. Online → name → dial 199406 → ready → start race
3. Wait for race to begin (countdown ends, you have controls)
4. Drive a few seconds — enough that gameplay heartbeats fire
5. Disconnect

Tell me when done. The next reply from me will be a SURGICAL fix backed by the data, not another guess.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
