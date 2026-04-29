# MicroMotorMayhem server — deploy notes

`mserver.py` is a standard-library Python 3 script. It runs alongside
the other Saturn NetLink revival servers (Disasteroids on 4822/9092,
Utenyaa on its own port) and listens on **4826** for SNCP traffic plus
**9094** for the local admin HTTP API.

## One-shot deploy (Windows-side scp/ssh)

From the repo root on your dev box:

```sh
scp tools/mmm_server/mserver.py gary@saturncoup.duckdns.org:/home/gary/mserver.py
scp tools/mmm_server/mmm.service gary@saturncoup.duckdns.org:/tmp/mmm.service

ssh gary@saturncoup.duckdns.org 'sudo mv /tmp/mmm.service /etc/systemd/system/mmm.service \
  && touch /home/gary/mmm_leaderboard.json \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now mmm.service \
  && sudo systemctl is-active mmm.service'
```

`is-active` should print `active` on the last line. If anything else,
inspect with `journalctl -u mmm.service -n 100 --no-pager`.

### File layout on host

| File                              | Purpose                                                          |
|-----------------------------------|------------------------------------------------------------------|
| `/home/gary/mserver.py`           | The server script (this repo's `mserver.py`).                    |
| `/etc/systemd/system/mmm.service` | systemd unit (this repo's `mmm.service`).                        |
| `/home/gary/mmm_leaderboard.json` | Persistent leaderboard. Touched empty on first run.              |

The leaderboard file is rewritten by the server after every race.
Back it up if you care about its contents.

## Updating

Replace `mserver.py` on the host and restart:

```sh
scp tools/mmm_server/mserver.py gary@saturncoup.duckdns.org:/home/gary/mserver.py
ssh gary@saturncoup.duckdns.org 'sudo systemctl restart mmm.service'
```

## Smoke test (admin API)

After the unit is active, from the host itself:

```sh
curl -H "X-Admin-Auth: nginx-verified" http://127.0.0.1:9094/api/state
curl -H "X-Admin-Auth: nginx-verified" http://127.0.0.1:9094/api/leaderboard
```

Both should return valid JSON. `/api/state` includes `players`, `bots`,
`match`, and `tuning` blocks. From a remote box, route through the
unified portal at `:9099` (see nginx snippet below).

## nginx admin proxy snippet

If your unified admin portal at `:9099` proxies `/admin/disasteroids/`
and `/admin/utenyaa/`, add the matching MMM block:

```nginx
location /admin/mmm/ {
    proxy_pass http://127.0.0.1:9094/;
    proxy_set_header Host $host;
    proxy_set_header X-Admin-Auth nginx-verified;
    proxy_set_header X-Real-IP $remote_addr;
}
```

Reload nginx with `sudo nginx -t && sudo systemctl reload nginx`.

## Admin endpoints

All endpoints require either `X-Admin-Auth: nginx-verified` (set by
the upstream proxy) or HTTP Basic auth (default
`admin` / `mmm2026` — change in the systemd unit's `ExecStart` if
desired).

| Method | Path                                | Purpose                                                 |
|--------|-------------------------------------|---------------------------------------------------------|
| GET    | `/api/state`                        | Snapshot of connections, bots, match, tunables.         |
| GET    | `/api/leaderboard`                  | Full leaderboard (top 50 + raw store).                  |
| POST   | `/api/add_bot`                      | Add a bot to the lobby.                                 |
| POST   | `/api/remove_bot`                   | Remove the last bot.                                    |
| POST   | `/api/force_end_race`               | End the current match immediately.                      |
| POST   | `/api/kick?player_id=N`             | Disconnect a player by `user_id`.                       |
| POST   | `/api/tune?key=K&value=V`           | Set a runtime knob (see below).                         |
| POST   | `/api/upload_waypoints?track_id=N`  | Upload real per-track waypoint table (see limitations). |

#### Upload waypoints (alpha 0.1 stop-gap)

`POST /api/upload_waypoints?track_id=N` accepts a JSON body of the form:

```json
{
  "total": 12,
  "waypoints": [
    {"section": 0, "x": 0,    "y": 0, "z": 1200},
    {"section": 0, "x": 850,  "y": 0, "z": 850},
    {"section": 0, "x": 1200, "y": 0, "z": 0}
  ],
  "checkpoints": []
}
```

Stored in the server-process `track_waypoints[track_id]` dict; `BotPlayer`
prefers it over the procedural oval fallback as soon as it's set. Resets on
restart (no on-disk persistence in alpha 0.1). Use this to hand-load real
waypoints during testing until alpha 0.2 wires up the proper Saturn upload
or a server-side `.bin` parser.

### Tunable knobs

| Key                    | Type | Default | Range      | Notes                                       |
|------------------------|------|---------|------------|---------------------------------------------|
| `bot_count`            | int  | 0       | 0..3       | Hint for default lobby; use add/remove_bot. |
| `powerup_respawn_secs` | int  | 8       | 0..120     | Time before a taken powerup re-rolls.       |
| `lap_count`            | int  | 3       | 1..9       | Laps required to win the next race.         |
| `random_track`         | bool | true    |            | If false, every race uses `forced_track_id`.|
| `forced_track_id`      | int  | 1       | 1..15      | Used only when `random_track` = false.      |

## Known limitations (alpha 0.1)

This release is a behavior-faithful port that intentionally strips a couple
of data dependencies that need a Saturn round-trip to wire up properly. The
server is fully usable for human-vs-human races; the items below only affect
server-side bots.

- **Bot waypoints are procedural, not track-accurate.** `BotPlayer` ports
  `cpu_control()` from `main.c:5159-5350` verbatim (atan2 target bearing,
  6 deg deadband, sign-mapped steering, edge-triggered powerup activation,
  unconditional gas, siren). The AI BEHAVIOR is identical to offline play.
  But the real per-track waypoint coordinates live in track `.bin` files on
  the Saturn-side disc, which the server can't read in alpha 0.1. The stub
  uses a procedurally-generated 12-point oval per track, so server-bot
  positions in `PLAYER_SYNC` packets will not align with the actual track
  collision/visual elements rendered on the Saturn. Bots race competently
  but visually drift off the road.

  **Mitigation:** the systemd unit ships with `--bots 0` so production
  matches default to human-only. Operators can hand-load real waypoint
  tables per track via `POST /api/upload_waypoints?track_id=N` during
  testing; once populated, bots will use the real geometry.

  **Roadmap (alpha 0.2):**
  - Option A: Saturn host uploads waypoint table to server on match start
    via a new `MNET_MSG_WAYPOINT_DATA` packet (~256 entries x 8 bytes = 2 KB).
  - Option B: server-side parser for the track `.bin` format
    (see `main.c:3822+` for the binary layout).

- **Bot powerups are not yet rolled server-side.** `BotPlayer.current_powerup`
  exists and is wired into the cpu_control edge-trigger correctly, but the
  server's powerup pickup path for bots isn't connected yet. Bots will not
  fire weapons in alpha 0.1; this is a pure data-flow gap, not an AI gap.

## Troubleshooting

- **"Address already in use"**: another service is bound to 4826/9094.
  Stop it or change the ports in `mmm.service` (`--port` / `--admin-port`).
- **Saturn never gets WELCOME**: the bridge's shared secret must match
  `MicroMotorMayhem2026!NetLink#Key` exactly. Mismatch will appear in
  the journal as `Wrong shared secret from ...`.
- **Race never ends**: confirm at least one human reaches `lap_count`.
  If everyone DNFs, the race ends automatically. Use
  `POST /api/force_end_race` to abort.
