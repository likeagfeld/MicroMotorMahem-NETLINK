# MicroMotorMayhem — NetLink Edition

**RC cars on 16 tracks, now online.** 4-player top-down racing over the Sega Saturn NetLink modem.

> 💝 **Huge thanks to [jberetta](https://github.com/jberetta82)** for the original MicroMotorMayhem — the engine, the physics, the 16 hand-built tracks, the cars, the HUD, the powerups, the years of Saturn-homebrew suffering it took to ship a Micro-Machines-on-Saturn racer in pure C with Jo Engine + SGL. This fork only *adds* a networking layer on top of jberetta's v0.3 codebase; everything you're actually playing — every car, every track, every collision tick — is their work. If you enjoy this port, go star the upstream repo: https://github.com/jberetta82/MicroMotorMahem-NETLINK

---

## NetLink Online Play

Online multiplayer for **up to 4 players** over the Sega Saturn NetLink modem, same connectivity method as Flicky's Flock, Utenyaa, Disasteroids and the rest of the NetLink revival lineup. Just update to the latest DreamPi or netlink.py PC tunnel script — the MMM entry is already shipped at dial code **199406**. No manual config required.

### What's in the box

- **Up to 4 players** with server-authoritative race state (laps, checkpoints, finish detection, leaderboard)
- **Client-authoritative local movement** for zero-lag steering — your car drives off your local pad, the server only validates lap/checkpoint progression
- **Server-rolled powerup types** so every Saturn sees the same boost / bomb / rockets / shrink / shield / spring / invincibility result from any given crate (replaces the offline `jo_random(7)` that would have desynced)
- **Server-picked random track** per match — picked from the full 16-track roster
- **8-car lobby roster** — cycle through the same car models the offline single-player picks from
- **3-lap races** matching the existing offline `MAX_LAPS` balance
- **Persistent online leaderboard** — WINS, BEST LAP, PODIUMS, RACES, POINTS — using MMM's existing `points_table[] = {0, 4, 3, 2, 1}` scoring
- **Custom name entry** with Saturn backup-RAM persistence (cartridge key `MMM_NAME`, 8 chars)
- **Local co-op + online**: plug a 2nd controller into the lobby and it registers as P2 on this Saturn — both local players appear on the same shared camera in online mode (split-screen is reserved for offline 2P VS only)
- **Z-overlay leaderboard** in the lobby — hold Z to see the standings without leaving
- **Client-to-server diagnostic logging** so the operator can see what your Saturn is doing if something goes wrong
- **Server-authoritative race finish** — first player to reach `MAX_LAPS` triggers `RACE_FINISH`, server grades remaining positions via lap > checkpoint > distance-to-next-waypoint tiebreak (same math as the offline `set_player_position`), broadcasts standings to every client. No ties, no client-side guessing.

### Flow

```
TITLE → Online Play → Name Entry (backup RAM) → Connecting → Lobby
  (ready, car cycle, P2 hot-plug, Z-overlay leaderboard)
  → Race (server-picked track, 3 laps, server-rolled powerups, finish detection)
  → Results → back to Lobby
```

### Lobby controls

| Button | Action |
|---|---|
| A / C  | Toggle ready |
| L / R  | Cycle car (8 models) |
| START  | Request race start |
| B      | Back to title (stay connected for quick rejoin) |
| Y      | Disconnect |
| Z      | Hold — leaderboard overlay |

### Leaderboard columns

| Column | Meaning |
|---|---|
| WINS | First-place finishes |
| BEST LAP | Single fastest lap across any track (shares the same `level_fastest_lap` field as offline time-attack) |
| PODIUMS | Top-3 finishes |
| RACES | Total races completed |
| POINTS | Cumulative points using MMM's existing `{0, 4, 3, 2, 1}` table |

### Local co-op + online

The same model Flicky's Flock and Utenyaa use. Plug a second controller into port B in the lobby and the row splits into "P1 + P2 (this Saturn)" — an `ADD_LOCAL_PLAYER` packet goes to the server and you'll occupy two slots on the next race start. Hot-unplug the 2nd pad mid-match and the Saturn cleanly reverts to single-camera fullscreen rendering. **Online mode never uses split-screen** — both local players share one camera for clarity over the link. Saturn-side split-screen remains for offline `GAMEMODE_2PLAYERVS` only.

---

## Building

Same toolchain as upstream — **Jo Engine + SGL** under the standard sh2eb-elf toolchain. This fork vendors a project-local copy of `jo_engine/` source and rebuilds it from scratch with `PRINTF=0` and `AUDIO=1` so that `jo_nbg2_print` and `jo_audio_init` both resolve (the bundled pre-built `.o` files in some upstream Jo Engine drops were compiled with `JO_COMPILE_WITH_PRINTF_SUPPORT` defined, which `#ifndef`-excludes the nbg2 functions MMM relies on for HUD/lobby text). See `DEPLOY.md` for the full Docker recipe; the short form is: build the Jo Engine vendored tree first, then `make` from the project root produces `game.iso` + `game.cue`.

---

## Credits

- **Upstream game:** [jberetta](https://github.com/jberetta82) (https://github.com/jberetta82/MicroMotorMahem-NETLINK). Engine, physics, all 16 tracks, the 8 cars, the powerup roster, the HUD, the AI, the audio integration — every gameplay-facing pixel and every collision tick is theirs.
- **NetLink online port (this fork):** [@likeagfeld](https://github.com/likeagfeld).

### Disclaimer

NetLink online functionality developed with assistance from AI (Claude). The underlying MicroMotorMayhem game code — engine, tracks, cars, physics, AI, audio, HUD, all the gameplay — is entirely jberetta's work. This fork only *adds* a networking layer on top. Please credit jberetta if you share gameplay.

---

## Upstream README (verbatim)

```
Micro Motor Mayhem V0.3
-----------------------

Changes:

increased gravity to make cars less floaty
fix to improve players direction after re-spawning
improved cpu players waypoints on pool table levels
added track map to HUD - can turn off in pause menu if in the way
new spring power up - press L trigger to bounce over obsticles (eg pool table pockets)
new invincibility power up - can't be hurt by other player powerups and destroy other players if hit
particle effect added to missile and speed boost
changed speed boost powerup so you have to activate it now by pressing left trigger
new track - cold case toilet
some minor changes to textures etc


MICRO MOTOR MAYHEM V0.2
------------------------------------

Changes:

Fixed cop car texture issue
Moved HUD to improve track visibility based on feedback
Added track layout to level select
Added fastest lap to level select
Removed turn speed adjustment to improve handling at slow speeds
Added music to all levels
Added results table to time attack
Fixed issue where it was still in split screen after exiting 2 player mode
Stopped music and timer when game paused


MICRO MOTOR MAYHEM V0.1
-----------------------

Basically a cross between the old Micro Machines games and Mario Kart. Race against 3 CPU players or against a friend!
It's still not totally finished so there will be bugs - please let me know if you find any.
Music isn't finished, only 2 levels so far - not sure if it needs in game music or not? - you can adjust the volume in the pause menu

Known issues bugs:
There's an odd bug with the sound effects before the race starts, not sure why.
some times power up boxes don't re-appear
CPU cars are bad at the pool table levels
physics are a bit crazy sometimes when hitting walls / other players
players can get stuck - can reset to last checkpoint in pause menu
not enough checkpoints so can be sent back quite far on some levels
current position shown not always accurate during race
runs slower on PAL consoles - working on a PAL version with adjusted speeds


Powerups:

Boost - increases speed for short period (might slow this down)
bomb - press LT to throw a bomb (short range)
rockets - press LT to fire rockets at other players
shrink - shrinks other players and slows them down for short period
shield - protects you against bombs, rockets and shrink


Controls:

A - Brake / Reverse
B - Accelerate
C - Horn
LT - Use Powerup
RT - look behind
Y - Change Camera
Start - Pause game

debug mode - press LT and RT together

game modes:

Practice: do as may laps as you want, no target
time attack: beat the target time in 3 laps, powerups are speed boost
1 Player Race: race against 3 CPU controlled players, 3 laps
2 Player VS: 2 player split screen head to head, 3 laps
```
