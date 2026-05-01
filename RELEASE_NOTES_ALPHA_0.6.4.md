# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.6.4)

Maximum-instrumentation diagnostic build. Goal is **one** test cycle that produces enough data to definitively diagnose the race-1/race-2 visual corruption — sprite VRAM, palette CRAM, and sprite-definition tables all dumped at PHASE_E.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem.

## How to play

Load `game.cue`. Title → **Online Play** → name → dial **199406**.

## What's new in 0.6.4

Read-only instrumentation only — does NOT change rendering behavior. Logs the following at race start (PHASE_E, right before countdown):

| Log line | What it captures | What "broken" looks like |
|---|---|---|
| `DIAG_RACE n=N t=T models=M sect=S wp=W` | Race counter, track id, post-load BIN totals | `models=0` or impossibly low → BIN didn't parse correctly |
| `DIAG_SPR last=N dynMAP=N dynHUD=N dynPUP=N` | jo_engine's `__jo_sprite_id` + the dynamic IDs game struct uses for runtime draws | `last < 175` after race start → sprite atlas got freed too aggressively. Mismatched dyn vs hardcoded ids → static ATTRIBUTE arrays in objects.h reference wrong sprites |
| `DIAG_DEF H:WxH@A P:WxH@A M:WxH@A` | width/height/VRAM-addr from `__jo_sprite_def[]` for HUD, PUP, MAP | width=0 for a populated sprite → VRAM allocator not advanced; adr=0 → never written |
| `DIAG_HC PL12:WxH@A PU144:WxH@A MP175:WxH@A` | Same fields but for the *hardcoded* compile-time IDs (PLAYER_TILESET=12, PUP_TILESET=144, MAP_TILESET=175) | If PL12 width != 32 (player tile size), or PU144 width != 16 (PUP tile size), or MP175 width != something sensible → the static `ATTRIBUTE` arrays in objects.h are pointing at wrong sprite IDs and producing the "wrong colors / mix of static" symptoms |
| `DIAG_PAL sky=N flr=N fnt=N prv=N trk=N` | Palette IDs for the five `jo_palette` instances | Duplicates → palette CRAM collision, sprite uses wrong colors |
| `DIAG_VR HUD@ADDR b=BB BB BB BB ...` | First 16 raw bytes of HUD's actual VDP1 sprite VRAM (cache-through alias 0x25C00000) | All `00`s → sprite never DMA'd to VRAM. Looks like ASCII or 4-byte-aligned-words → wrong region got copied here. Sane palette indices → sprite is correctly there |
| `DIAG_VR MAP@ADDR b=BB ...` | Same for MAP sprite | Same diagnosis as HUD but for track-tileset-specific corruption |
| `DIAG_XP xp0=P xp1=P xp2=P p0=W p1=W` | First three `xpdata_` pointer values + first dword behind each | Pointers should be in WORK_RAM_LOW range (0x00200000+). Garbage values → load_level didn't populate. p0=00000000 → BIN parse failed |
| `DIAG_SPR_DRIFT f=F prev->cur` | (gameplay heartbeat, only fires on change) | Should NEVER fire during a race. If it does → something is allocating new sprites mid-race and overflowing VDP1 VRAM |

## Token bucket bumped 32 → 64

The race-start burst is ~26 log messages. With the 32-token bucket some would have been dropped silently. Bumped to 64 for this diagnostic build so we capture the full picture in one cycle.

## Test plan — ONE CYCLE ONLY

1. Cold boot
2. Online → name → dial 199406 → ready → start race
3. Drive at least 1 lap (cross start a couple of times) so we get gameplay heartbeats too
4. **Note in your head exactly what looks broken visually** — track polygons missing? Wrong colors on cars? Sky leaking? Static-y blocks? — but don't write it down yet, just remember
5. Race ends OR you back out — disconnect
6. Tell me you're done

I pull all logs from server, cross-reference your visual observation with the hardware-state dump, and the next thing I send back is either:
- "It's X — here's the surgical fix" (high confidence)
- "I need *one* more specific test of Y" (only if the data is genuinely ambiguous)

No more guesses, no more "defensive" patches.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — engine, tracks, cars, physics, powerups, AI, audio — is entirely jberetta's work.
