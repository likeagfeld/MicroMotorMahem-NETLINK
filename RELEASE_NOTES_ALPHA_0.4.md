# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.4)

Fourth alpha. Controls now work for everyone (the 0.3 layered fix had its gamepad pinning clobbered by `create_player`, leaving non-slot-0 players unable to drive). Lap counter no longer clipped at the bottom of the screen. Server-side stall detection ensures races always reach a winner.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` (`[server:199406]`). The server is already live; just dial in from the Saturn.

## How to play

Load `game.cue` (not `game.iso` directly) on your emulator or ODE. From the title screen pick **Online Play**, enter a name (1–8 characters, persisted via Saturn backup RAM key `MMM_NAME`), and dial **199406**. Press A or C to ready up, START to request a race, server picks a random track from the 16-track roster.

## Critical fixes shipped in this build

| Bug | Root cause | Fix |
|---|---|---|
| No buttons work for local players in 2H online race | `create_player()` runs late in `mmm_online_start_race` and unconditionally rewrites `players[].gamepad` with its offline scheme (slot 0 → port 0, slot 1 → port 15 for 2P; or 0/1/2/3 for 4P). My earlier gamepad-pinning that ran BEFORE `create_player` was getting clobbered, so any local user not in slot 0 ended up with their controller pointed at an unused multitap port — no keypress reached their car. | Split the slot resolution. `s_is_local[]` + `target_player` are still set BEFORE `create_player` (downstream code reads them). Gamepad pinning moved to AFTER `create_player` so it has the last word: target_player → port 0, other local-coop slot → port 15, non-local slots → port 7. |
| Lap counter clipped at bottom of screen | Upstream draws lap digits at y=103 with `jo_sprite_change_sprite_scale(2)` — at 2x scale the 16-px sprite becomes 32 px tall, putting its bottom edge at y=119, past Saturn's visible y=112 boundary. | Shifted the target_player HUD lap block from y=103 to y=95. Bottom edge now at y=111, just inside visible area. |
| Race could run forever if a player's car got stuck against geometry | `check_race_finish()` only ended the race when "any human finishes OR no non-terminal players". A player wedged against a wall but still alive + sending PLAYER_STATE was non-terminal, so the race stayed open even if every other player had finished. | New `tick_stall()` runs every server tick. `Player.last_progress_time` is bumped by every checkpoint or lap advance; if 90 sec passes with no progress, the player becomes DNF. Logs `[INFO] Player pid=N stalled — DNF` to journald. |
| 1H+1B race never ends because the bot's procedural-oval AI doesn't cross real-track checkpoints | Same `check_race_finish()` rule: if the lone human gets stuck and the bot keeps "racing" forever in its 12-point oval, neither finishes nor times out. | New `MAX_RACE_DURATION = 600s` (10-minute) hard cap in `tick_stall()`. After the cap, ALL non-finished players become DNF and the existing race-end path fires with whoever has the best position. |

## What's IN alpha 0.4

- All alpha 0.3 functionality (gamepad/camera/local-slot mapping, P1 PLAYER_STATE producer pid fix, online pause-menu disabled, Disasteroids-style name entry)
- Controls actually work for primary AND local-coop P2 in online races
- Lap counter visible on screen
- Server-side race-always-terminates safeties (per-player stall + global duration cap)
- Server still has the implicit-lap-from-PLAYER_STATE detection so you don't need a Saturn-side LAP_COMPLETE send to make the race end

## Known limitations carried into alpha 0.5

- **P2 PLAYER_STATE broadcast not yet sent.** Local rendering of P2 on YOUR Saturn works (your second controller drives the second car on this Saturn), but the server doesn't receive PLAYER_STATE_P2 packets from us, so other Saturns can't see your P2 driving and the server tracks P2 only via implicit-lap-from-PLAYER_STATE which doesn't fire because we never send P2 state. Fix path: factor the 16-line P2 producer block into `mmm_net.c` so `main.o` doesn't grow past the title-render byte threshold.
- **HUD top-center sprite distortion in some online races** — same brittle `HUD_TILESET=144` hardcode pattern as the title's old `MAP_TILESET=175`. Fix path: replace `HUD_TILESET+N` references in `draw_hud()` with `game.hud_sprite_id+N` (the actual return value from the HUD.TGA load), so it's robust to sprite-allocation drift.
- **Bot AI still uses procedural waypoints, not the actual track geometry.** Real per-track waypoints live in the `.bin` files on disc and aren't uploaded to the server yet. Bots are visible, hittable, consistent across consoles — but they steer in a generic oval pattern.
- **Split-screen P2 view missing 3D + background** during local-coop online — under investigation.
- **Race timer auto-advances** the leaderboard back to the lobby after ~4 seconds (RACE_END_TIMER=120 frames at 30 fps); not driven by button press. If you'd prefer "press any button to continue", that's a one-line change.
- 5-player+ matches not exercised yet (cap is `MNET_MAX_PLAYERS = 4`).

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, tracks, cars, physics, powerups, AI, and audio — is entirely the work of jberetta. Please credit them if you share gameplay.
