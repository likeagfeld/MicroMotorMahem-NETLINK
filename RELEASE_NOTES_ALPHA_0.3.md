# MicroMotorMayhem NetLink — Online Multiplayer (Alpha 0.3)

Third alpha. The "human-vs-human actually works" pass: producer-side pid mapping is now correct, so remote players visibly drive around the track instead of appearing frozen at the start line. Lap counter increments properly on every Saturn. Name-entry layout cleaned up — every letter is on screen.

💝 **Massive thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the 16 tracks, the 8 cars, the physics, the powerups, the HUD, the AI, the audio. This fork only *adds* a networking layer; every line of gameplay code and every track you're actually racing on is jberetta's work. Give the upstream a star: https://github.com/jberetta82/MicroMotorMahem-NETLINK

## Online setup

No manual config editing needed. Update to the **latest DreamPi** or **latest netlink.py PC tunnel script** — the MicroMotorMayhem entry is already shipped in the default `netlink_config.ini` (`[server:199406]` → `saturncoup.duckdns.org:4826`). The server is already live; just dial in from the Saturn.

## How to play

Load `game.cue` (not `game.iso` directly) on your emulator or ODE. From the title screen pick **Online Play**, enter a name (1–8 characters, persisted via Saturn backup RAM key `MMM_NAME`), and dial **199406**. Press A or C to ready up, START to request a race, server picks a random track from the 16-track roster.

## Critical fixes shipped in this build

| Bug | Root cause | Fix |
|---|---|---|
| Remote players appeared frozen at the start line during a 2-human race | `mnet_send_player_state` was reading `players[g_mnet.my_player_id]` — using the server-assigned NET PID as a slot index. With server-assigned pid=1 and the human in `players[0]`, Saturn was broadcasting *the bot's* (or other player's) state every frame as its own. Other Saturns received zeroed/stale data and rendered the remote at start. | Cache the local-slot index in `s_my_primary_slot` / `s_my_p2_slot` during `mmm_online_start_race`, read `players[s_my_primary_slot]` for the producer. Symmetric to the consumer-side `s_net_player_id[p]` lookup that was already correct for `remote_states`. |
| Lap counter stuck at 1 | Saturn-side `mnet_send_lap_complete()` was defined but never called from the lap-cross detection path. Server's `p.lap` stayed at 0; HUD shows local-only lap which never advanced past the initial start-line trigger because the race-finish handler reset things weirdly. | After `players[p].laps++` in `player_collision_handling`, send `MNET_MSG_LAP_COMPLETE` (gated to our primary local slot). Server validates monotonic progression, broadcasts `LAP_NOTIFY`, and triggers `RACE_FINISH` on first player to reach `lap_count`. Server-side belt-and-suspenders implicit lap detection from `PLAYER_STATE.lap` stays in place. |
| Name-entry letters hidden / clipped off-screen | The 3-char-wide-cell grid (` X ` / `>X<` per letter) plus the right-side selection-mirror panel at column 28 was overlapping with the digit row's letters, and the per-cell layout pushed wider rows close to the 40-cell screen edge. | Rewrote layout to mirror Disasteroids/Flicky: each row is one space-separated string ("A B C D E F G H I"), `>` left-marker on the active row, `-` underline below the selected character. Removed the right-side selection panel entirely. All rows fit within the safe area. |

## What's IN alpha 0.3

- All alpha 0.2 functionality, plus the three fixes above
- 2-human online play where each Saturn correctly drives its own car and sees the other player drive around the track in near-real-time
- Lap counter advances 1 → 2 → 3, race ends when first player crosses lap 3 finish line
- Name-entry screen with clean Disasteroids-style layout that fits the screen on real Saturn hardware

## Known limitations carried into alpha 0.4

- **Bot AI uses procedural waypoints, not the actual track geometry.** Real per-track waypoints live in the `.bin` files on disc and aren't uploaded to the server yet. Bots are visible, hittable, and consistent across consoles, but they steer in a generic oval pattern rather than following the actual track. The admin endpoint `POST /api/upload_waypoints?track_id=N` is stubbed for the 0.4 fix.
- **HUD top-center distortion in some online matches** — sprite-slot misalignment that shows up only in some online sessions. Under investigation.
- **Split-screen P2 view missing 3D + background** during local-coop online — under investigation.
- 5-player+ matches not exercised yet (cap is `MNET_MAX_PLAYERS = 4`).
- Disconnect prompt is single-Y without confirmation overlay.

## Disclaimer

Online NetLink functionality implemented with assistance from AI (Claude). The underlying MicroMotorMayhem game — its engine, tracks, cars, physics, powerups, AI, and audio — is entirely the work of jberetta. Please credit them if you share gameplay.
