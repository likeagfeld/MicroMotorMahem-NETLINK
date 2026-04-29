#!/usr/bin/env python3
"""
MicroMotorMayhem NetLink Game Server

Manages online multiplayer for MMM. Mirrors the Disasteroids server
architecture: bridge-authenticated TCP connections, SNCP binary framing,
lobby management, and a server-authoritative tick at 20 Hz.

Networking model: PASSTHROUGH with server-auth game-critical state.
  Client-auth: local movement, local wall collision, SFX
  Server-auth: powerup spawn-type roll, CPU car selection, lap/checkpoint
               validation, race finish, leaderboard

Usage:
    python3 mserver.py --port 4826 --bots 0 --admin-port 9094
"""

import argparse
import base64
import json
import logging
import math
import os
import queue
import random
import select
import socket
import struct
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mmm_server")

# ==========================================================================
# Constants
# ==========================================================================

HEARTBEAT_TIMEOUT = 60.0
MAX_RECV_BUFFER = 8192
USERNAME_MAX_LEN = 16
UUID_LEN = 36

# Bridge authentication
SHARED_SECRET = b"MicroMotorMayhem2026!NetLink#Key"
AUTH_MAGIC = b"AUTH"
AUTH_OK = 0x01
AUTH_TIMEOUT = 5.0

MAX_BRIDGES = 8
MAX_PLAYERS = 4   # MMM: up to 4 racers
MIN_PLAYERS = 2   # need 2+ slots to start (one Saturn with local-coop satisfies this)

# Track: random pick from indices 1..15 (0 = TITLE; skip it)
TRACK_MIN = 1
TRACK_MAX = 15

# Lap count default
DEFAULT_LAP_COUNT = 3

# Powerup slot count and type range
POWERUP_SLOTS = 8
POWERUP_TYPE_MIN = 1   # 1=boost, 2=bomb, 3=rocket, 4=shrink, 5=shield, 6=spring, 7=invuln
POWERUP_TYPE_MAX = 7
POWERUP_RESPAWN_DEFAULT = 8.0  # seconds before a taken powerup re-rolls

# Position points table (mirrors MMM points_table[] = {0,4,3,2,1})
POINTS_TABLE = [0, 4, 3, 2, 1]

# SNCP Auth Messages
MSG_CONNECT = 0x01
MSG_SET_USERNAME = 0x02
MSG_HEARTBEAT = 0x04
MSG_DISCONNECT = 0x05

MSG_USERNAME_REQUIRED = 0x81
MSG_WELCOME = 0x82
MSG_WELCOME_BACK = 0x83
MSG_USERNAME_TAKEN = 0x84

# MMM Messages — Client -> Server (mirrors mmm_protocol.h)
MNET_MSG_READY = 0x10
MNET_MSG_INPUT_STATE = 0x11
MNET_MSG_START_GAME_REQ = 0x12
MNET_MSG_CAR_SELECT = 0x13
MNET_MSG_PLAYER_STATE = 0x14
MNET_MSG_POWERUP_PICKUP = 0x15
MNET_MSG_ADD_LOCAL_PLAYER = 0x16
MNET_MSG_REMOVE_LOCAL_PLAYER = 0x17
MNET_MSG_LEADERBOARD_REQ = 0x18
MNET_MSG_LAP_COMPLETE = 0x19
MNET_MSG_HEARTBEAT_GAME = 0x1A
MNET_MSG_CLIENT_LOG = 0x1B   # [level:1][text_len:1][text:N] — client diag

# Mirrors mmm_protocol.h MNET_LOG_LEVEL_*
CLIENT_LOG_LEVEL_NAMES = {0: "DEBUG", 1: "INFO", 2: "WARN", 3: "ERROR"}
CLIENT_LOG_TEXT_MAX = 80

# MMM Messages — Server -> Client
MNET_MSG_LOBBY_STATE = 0xA0
MNET_MSG_GAME_START = 0xA1
MNET_MSG_INPUT_RELAY = 0xA2
MNET_MSG_PLAYER_JOIN = 0xA3
MNET_MSG_PLAYER_LEAVE = 0xA4
MNET_MSG_GAME_OVER = 0xA5
MNET_MSG_LOG = 0xA6
MNET_MSG_LOCAL_PLAYER_ACK = 0x86
MNET_MSG_PLAYER_SYNC = 0xA9
MNET_MSG_POWERUP_SPAWN = 0xAA
MNET_MSG_POWERUP_DESTROY = 0xAB
MNET_MSG_POWERUP_EFFECT = 0xAC
MNET_MSG_LAP_NOTIFY = 0xAD
MNET_MSG_RACE_FINISH = 0xAE
MNET_MSG_LEADERBOARD_DATA = 0xAF

# Input bitmask (mmm_protocol.h)
MNET_INPUT_GAS = 1 << 0
MNET_INPUT_BRAKE = 1 << 1
MNET_INPUT_LEFT = 1 << 2
MNET_INPUT_RIGHT = 1 << 3
MNET_INPUT_ACTION = 1 << 4
MNET_INPUT_START = 1 << 5
MNET_INPUT_HORN = 1 << 6

# Bot names
BOT_NAMES = [
    "PIXIE", "AXEL", "ZIPPER", "TURBO",
    "DRIFT", "RACER", "BOLT", "DASH",
    "SPARK", "VROOM", "RUSTY", "NITRO",
]

# ==========================================================================
# Client-Log writer (CLIENT_LOG opcode receiver)
# ==========================================================================
#
# Each authenticated connection can send MNET_MSG_CLIENT_LOG packets up to
# the server. We append them to mmm_client.log next to mserver.py with
# size-based rotation (5 MB → .1, keeping last 3 rotations) and mirror to
# stdout so journald captures them when running under systemd.
#
# The writer is kept tiny on purpose: a plain rotating file with a thread
# lock. Python's logging.handlers.RotatingFileHandler would also work but
# pulling it in just for this drags in a separate logger object that
# wouldn't share format with the existing `log` logger; this gives us
# exact control over the line format the user asked for.

CLIENT_LOG_FILENAME = "mmm_client.log"
CLIENT_LOG_MAX_BYTES = 5 * 1024 * 1024
CLIENT_LOG_KEEP_ROTATIONS = 3


class ClientLogWriter:
    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, CLIENT_LOG_FILENAME)
        self._lock = threading.Lock()

    def write(self, level: int, player_id, username: str, text: str):
        level_name = CLIENT_LOG_LEVEL_NAMES.get(level, "L%d" % level)
        # Sanitize: client text should already be ASCII per encoder, but a
        # bad bridge / corrupt frame might leak control chars — strip them.
        clean = "".join(c if 0x20 <= ord(c) < 0x7F else "?"
                        for c in text)[:CLIENT_LOG_TEXT_MAX]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        pid_str = str(player_id) if player_id is not None else "?"
        name_str = username if username else "?"
        line = "[%s] [%s] [pid=%s name=%s] %s" % (
            ts, level_name, pid_str, name_str, clean)
        with self._lock:
            self._rotate_if_needed()
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                log.error("client_log write failed: %s", e)
        # Mirror to stdout via the main logger — journald picks this up.
        # Map client levels to Python logging levels so warn/error stand out.
        if level >= 3:
            log.error("[CLIENT %s] %s", name_str, clean)
        elif level == 2:
            log.warning("[CLIENT %s] %s", name_str, clean)
        else:
            log.info("[CLIENT %s] %s", name_str, clean)

    def tail(self, n: int) -> list:
        """Return last n lines from the log file (best-effort)."""
        if n <= 0:
            return []
        try:
            with open(self.path, "rb") as f:
                # Cheap tail: read last ~n*200 bytes (lines are ~150-180
                # chars typical) then split. Good enough up to lines=1000.
                f.seek(0, os.SEEK_END)
                size = f.tell()
                read = min(size, max(n * 256, 16384))
                f.seek(size - read)
                data = f.read()
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            return lines[-n:]
        except (OSError, ValueError):
            return []

    def _rotate_if_needed(self):
        try:
            sz = os.path.getsize(self.path)
        except OSError:
            return
        if sz < CLIENT_LOG_MAX_BYTES:
            return
        # Shift .K → .K+1 from the highest down; drop anything past KEEP.
        for i in range(CLIENT_LOG_KEEP_ROTATIONS, 0, -1):
            src = "%s.%d" % (self.path, i - 1) if i > 1 else self.path
            dst = "%s.%d" % (self.path, i)
            if i == CLIENT_LOG_KEEP_ROTATIONS:
                # Drop the oldest rotation if it exists.
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError as e:
                    log.error("client_log rotate failed: %s", e)
                    return


# Module-level singleton — instantiated by MMMServer at startup.
client_log_writer = None  # type: ignore


# ==========================================================================
# Join-History writer (connect / disconnect / kick / auth events)
# ==========================================================================
#
# Records a rolling window of player lifecycle events to mmm_join_history.json
# next to mserver.py. Writes are atomic (tmp + os.replace) so a crash mid-write
# leaves either the previous file intact or the new file complete. Events are
# capped at the last JOIN_HISTORY_MAX entries (oldest dropped).
#
# Schema: list of {"ts": ISO8601, "event": str, "name": str, "ip": str,
#                  "player_id": int|None, "reason": str}

JOIN_HISTORY_FILENAME = "mmm_join_history.json"
JOIN_HISTORY_MAX = 1000


class JoinHistoryWriter:
    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, JOIN_HISTORY_FILENAME)
        self._lock = threading.Lock()
        self.events = []      # list of dict, oldest-first
        self.names_seen = set()
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                cleaned = []
                for ev in data:
                    if isinstance(ev, dict) and "event" in ev:
                        cleaned.append(ev)
                        nm = ev.get("name") or ""
                        if nm:
                            self.names_seen.add(nm)
                self.events = cleaned[-JOIN_HISTORY_MAX:]
                log.info("Join history: loaded %d events from %s",
                         len(self.events), self.path)
        except FileNotFoundError:
            self.events = []
        except (OSError, ValueError, json.JSONDecodeError) as e:
            log.warning("Join history: corrupt or unreadable (%s) — starting fresh", e)
            self.events = []

    def append(self, event: str, name: str = "", ip: str = "",
               player_id=None, reason: str = ""):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        entry = {
            "ts": ts,
            "event": str(event),
            "name": str(name or ""),
            "ip": str(ip or ""),
            "player_id": player_id if isinstance(player_id, int) else None,
            "reason": str(reason or ""),
        }
        with self._lock:
            self.events.append(entry)
            if len(self.events) > JOIN_HISTORY_MAX:
                self.events = self.events[-JOIN_HISTORY_MAX:]
            if entry["name"]:
                self.names_seen.add(entry["name"])
            self._flush_locked()

    def _flush_locked(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.events, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            log.error("join_history write failed: %s", e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def recent(self, limit: int) -> list:
        if limit <= 0:
            return []
        with self._lock:
            tail = list(self.events[-limit:])
        tail.reverse()  # newest-first
        return tail

    def total_connects_today(self) -> int:
        today = time.strftime("%Y-%m-%d", time.localtime())
        with self._lock:
            return sum(1 for e in self.events
                       if e.get("event") == "connect"
                       and isinstance(e.get("ts"), str)
                       and e["ts"].startswith(today))

    def unique_names_count(self) -> int:
        with self._lock:
            return len(self.names_seen)


# Module-level singleton — instantiated by MMMServer at startup.
join_history_writer = None  # type: ignore


# ==========================================================================
# SNCP Framing
# ==========================================================================


def encode_frame(payload: bytes) -> bytes:
    """Wrap payload in SNCP length-prefixed frame ([LEN_HI][LEN_LO][PAYLOAD])."""
    return struct.pack("!H", len(payload)) + payload


def encode_lp_string(s: str) -> bytes:
    """Encode a length-prefixed string ([len:1][bytes])."""
    raw = s.encode("utf-8")[:255]
    return struct.pack("B", len(raw)) + raw


def encode_uuid(uuid_str: str) -> bytes:
    raw = uuid_str.encode("ascii")[:UUID_LEN]
    return raw.ljust(UUID_LEN, b'\x00')


def s16(v: int) -> int:
    """Saturate to int16 range."""
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return int(v)


def u16(v: int) -> int:
    if v > 65535:
        return 65535
    if v < 0:
        return 0
    return int(v)


# ==========================================================================
# Message Builders
# ==========================================================================


def build_username_required() -> bytes:
    return encode_frame(bytes([MSG_USERNAME_REQUIRED]))


def build_welcome(user_id: int, uuid_str: str, username: str) -> bytes:
    payload = (bytes([MSG_WELCOME])
               + struct.pack("B", user_id & 0xFF)
               + encode_uuid(uuid_str)
               + encode_lp_string(username))
    return encode_frame(payload)


def build_welcome_back(user_id: int, uuid_str: str, username: str) -> bytes:
    payload = (bytes([MSG_WELCOME_BACK])
               + struct.pack("B", user_id & 0xFF)
               + encode_uuid(uuid_str)
               + encode_lp_string(username))
    return encode_frame(payload)


def build_username_taken() -> bytes:
    return encode_frame(bytes([MSG_USERNAME_TAKEN]))


def build_lobby_state(players: list) -> bytes:
    """[count:1][{id:1, name:LP, car:1, ready:1, is_local:1}...]"""
    count = min(len(players), MAX_PLAYERS)
    payload = bytes([MNET_MSG_LOBBY_STATE, count])
    for p in players[:count]:
        payload += struct.pack("B", p["id"] & 0xFF)
        payload += encode_lp_string(p["name"])
        payload += struct.pack("B", p.get("car", 0) & 0xFF)
        payload += struct.pack("B", 1 if p.get("ready") else 0)
        payload += struct.pack("B", 1 if p.get("is_local") else 0)
    return encode_frame(payload)


def build_game_start(seed: int, my_player_id: int, opponent_count: int,
                     track_id: int, lap_count: int) -> bytes:
    """[seed:4 BE][my_player_id:1][opp_count:1][track_id:1][lap_count:1]"""
    payload = bytes([MNET_MSG_GAME_START])
    payload += struct.pack("!I", seed & 0xFFFFFFFF)
    payload += bytes([my_player_id & 0xFF, opponent_count & 0xFF,
                      track_id & 0xFF, lap_count & 0xFF])
    return encode_frame(payload)


def build_input_relay(player_id: int, frame_num: int, input_bits: int) -> bytes:
    """[player_id:1][frame:2 BE][input:1]"""
    payload = bytes([MNET_MSG_INPUT_RELAY, player_id & 0xFF])
    payload += struct.pack("!H", frame_num & 0xFFFF)
    payload += bytes([input_bits & 0xFF])
    return encode_frame(payload)


def build_player_join(player_id: int, name: str) -> bytes:
    payload = bytes([MNET_MSG_PLAYER_JOIN, player_id & 0xFF])
    payload += encode_lp_string(name)
    return encode_frame(payload)


def build_player_leave(player_id: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_PLAYER_LEAVE, player_id & 0xFF]))


def build_game_over(winner_id: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_GAME_OVER, winner_id & 0xFF]))


def build_log(text: str) -> bytes:
    raw = text.encode("utf-8")[:255]
    payload = bytes([MNET_MSG_LOG, len(raw)]) + raw
    return encode_frame(payload)


def build_local_player_ack(player_id: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_LOCAL_PLAYER_ACK, player_id & 0xFF]))


def build_player_sync(player_id: int, x: int, y: int, z: int,
                      ry: int, speed: int,
                      lap: int, checkpoint: int, cur_wp: int,
                      dist_wp: int) -> bytes:
    """[pid:1][x:2s BE][y:2s BE][z:2s BE][ry:2s BE][speed:2s BE][lap:1][cp:1][cur_wp:1][dist_wp:2 BE]"""
    payload = bytes([MNET_MSG_PLAYER_SYNC, player_id & 0xFF])
    payload += struct.pack("!hhhhh",
                           s16(x), s16(y), s16(z),
                           s16(ry), s16(speed))
    payload += bytes([lap & 0xFF, checkpoint & 0xFF, cur_wp & 0xFF])
    payload += struct.pack("!H", u16(dist_wp))
    return encode_frame(payload)


def build_powerup_spawn(slot: int, ptype: int, x: int, y: int, z: int) -> bytes:
    """[slot:1][type:1][x:2 BE][y:2 BE][z:2 BE]
    NOTE: x/y/z fields are signed-int16 on the wire (Saturn world coords are
    Sint16). Encoded big-endian as `!hhh` to match the C client decoder."""
    payload = bytes([MNET_MSG_POWERUP_SPAWN, slot & 0xFF, ptype & 0xFF])
    payload += struct.pack("!hhh", s16(x), s16(y), s16(z))
    return encode_frame(payload)


def build_powerup_destroy(slot: int, taker_id: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_POWERUP_DESTROY,
                               slot & 0xFF, taker_id & 0xFF]))


def build_powerup_effect(player_id: int, effect: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_POWERUP_EFFECT,
                               player_id & 0xFF, effect & 0xFF]))


def build_lap_notify(player_id: int, lap: int, position: int) -> bytes:
    return encode_frame(bytes([MNET_MSG_LAP_NOTIFY,
                               player_id & 0xFF,
                               lap & 0xFF,
                               position & 0xFF]))


def build_race_finish(winner_id: int, standings: list) -> bytes:
    """standings: list of (pid, position, total_time_secs)."""
    count = min(len(standings), MAX_PLAYERS)
    payload = bytes([MNET_MSG_RACE_FINISH, winner_id & 0xFF, count])
    for pid, pos, total_time in standings[:count]:
        payload += bytes([pid & 0xFF, pos & 0xFF])
        payload += struct.pack("!H", u16(total_time))
    return encode_frame(payload)


def build_leaderboard_data(entries: list) -> bytes:
    """[count:1]{[name_len:1][name:N][wins:2BE][best_lap:2BE][podiums:2BE][races:2BE][points:2BE]}..."""
    count = min(len(entries), 10)
    payload = bytes([MNET_MSG_LEADERBOARD_DATA, count])
    for e in entries[:count]:
        name_bytes = e["name"].encode("utf-8")[:16]
        payload += struct.pack("B", len(name_bytes)) + name_bytes
        payload += struct.pack("!HHHHH",
                               u16(e.get("wins", 0)),
                               u16(e.get("best_lap", 0)),
                               u16(e.get("podiums", 0)),
                               u16(e.get("races", 0)),
                               u16(e.get("points", 0)))
    return encode_frame(payload)


# ==========================================================================
# Player + Bot
# ==========================================================================


class Player:
    """Game-side player state. One per simulation slot."""

    def __init__(self, player_id: int, name: str, car_id: int = 0,
                 is_bot: bool = False, is_local_p2: bool = False):
        self.player_id = player_id
        self.name = name
        self.car_id = car_id
        self.is_bot = is_bot
        self.is_local_p2 = is_local_p2

        # Spatial — Sint16 raw (Saturn world coords). Server tracks these
        # passively from PLAYER_STATE updates (and actively for bots).
        self.x = 0
        self.y = 0
        self.z = 0
        self.ry = 0
        self.speed = 0  # int16 on wire; for bots we keep it integer-scaled

        # Race state
        self.lap = 0
        self.current_checkpoint = 0
        self.current_waypoint = 0
        self.dist_to_next_waypoint = 0
        self.position = 0
        self.start_time = 0.0
        self.current_time = 0.0
        self.best_lap_time = 0  # in seconds; 0 = unset
        self.points = 0  # placement points this race
        self.total_points = 0  # accumulated points (server-tracked over session)
        self.dnf = False
        self.finished = False
        self.finish_time = 0.0  # absolute server time when crossed line on final lap

        # Networking
        self.last_input_bits = 0
        self.last_input_frame = 0
        self.last_state_recv_time = 0.0


# BotPlayer ports MicroMotorMayhem's cpu_control() AI from main.c:5159-5350 verbatim:
# - same atan2 target bearing, 6 deg deadband, sign-mapped steering
# - same unconditional cpu_gas, edge-triggered cpu_action when powerup>0
# - same waypoint advance when within distance threshold
#
# LIMITATION (alpha 0.1): Real per-track waypoint coordinates live in track .bin
# files on the Saturn-side disc. The server uses procedurally-generated 12-point
# ovals as a stub. Bot AI BEHAVIOR is identical to offline cpu_control(); only
# the GEOMETRY differs. Result: bots race competently but their positions on
# server-rendered PLAYER_SYNC packets won't align with track collision/visual
# elements on the Saturn.
#
# Roadmap (alpha 0.2):
# - Option A: Saturn host uploads waypoint table to server on match start via a
#   new MNET_MSG_WAYPOINT_DATA packet (256 waypoint entries x 8 bytes = 2 KB upload)
# - Option B: Server-side parser for the track .bin format (see main.c:3822+
#   for the binary layout - model_total + per-model {points/polygons/...})
#
# Default --bots 0 in service config until this is wired.
#
# Operators can hand-load real waypoints during testing via the admin endpoint
# POST /api/upload_waypoints?track_id=N (see admin handler) which populates
# track_waypoints[track_id]; tick_ai prefers that table when present.
_TRACK_LOOP_CACHE = {}

# Operator-uploaded real waypoint tables, keyed by track_id. Populated by
# POST /api/upload_waypoints. Each entry is a list of (x, y, z) tuples.
# tick_ai/update_waypoint prefer this over the procedural oval stub.
track_waypoints = {}


def _get_track_waypoints(track_id: int) -> list:
    """Return list of (x, y, z) tuples for track waypoints.

    Prefers operator-uploaded real waypoints from `track_waypoints` (loaded via
    the admin upload endpoint). Falls back to the procedural 12-point oval stub
    when no real data is available for this track. The procedural fallback is
    NOT track-accurate; see the BotPlayer module-level comment block above.
    """
    real = track_waypoints.get(track_id)
    if real:
        return real
    if track_id in _TRACK_LOOP_CACHE:
        return _TRACK_LOOP_CACHE[track_id]
    rng = random.Random(0x4000 + track_id)
    n = 12
    cx = rng.randint(-200, 200)
    cz = rng.randint(-200, 200)
    rx = rng.randint(800, 1500)
    rz = rng.randint(800, 1500)
    pts = []
    for i in range(n):
        ang = (i / n) * 2.0 * math.pi
        x = int(cx + rx * math.cos(ang))
        z = int(cz + rz * math.sin(ang))
        pts.append((x, 0, z))
    _TRACK_LOOP_CACHE[track_id] = pts
    return pts


class BotPlayer(Player):
    """Server-side AI driver. Generates INPUT_STATE via cpu_control() port,
    then injects the bits via INPUT_RELAY just like a remote human.

    Faithful port of main.c:5159-5350 cpu_control() — see the module-level
    comment block above for the full behavior breakdown and the alpha 0.1
    waypoint-data limitation.
    """

    BOT_SPEED = 12  # int16 world units per server tick (tuned by feel)
    WAYPOINT_RADIUS = 100  # world units to "reach" a waypoint
    TURN_DEADBAND = 6  # degrees - matches MMM's |rot_dif| <= 6 deadband

    def __init__(self, player_id: int, name: str, bot_idx: int):
        super().__init__(player_id, name, car_id=bot_idx & 0x07,
                         is_bot=True, is_local_p2=False)
        self.bot_idx = bot_idx
        self.next_waypoint = 0
        self.frame = 0
        self.last_relay_bits = 0xFF  # force first relay
        self.relay_force_counter = 0
        # Mirrors cpu_pressed_action in players[] — edge-trigger guard so
        # MNET_INPUT_ACTION fires once per powerup pickup, not every tick.
        self.cpu_pressed_action = False
        # Server-side mirror of players[p].current_powerup. Set by the powerup
        # roll path when a bot picks up a box; cleared when consumed.
        self.current_powerup = 0

    def reset_for_track(self, track_id: int):
        wps = _get_track_waypoints(track_id)
        self.next_waypoint = 1
        self.current_waypoint = 0
        self.current_checkpoint = 0
        self.lap = 0
        self.x = wps[0][0]
        self.y = wps[0][1]
        self.z = wps[0][2]
        self.ry = 0
        self.speed = 0
        self.frame = 0
        self.last_relay_bits = 0xFF
        self.relay_force_counter = 0
        self.dnf = False
        self.finished = False
        self.points = 0
        self.cpu_pressed_action = False
        self.current_powerup = 0

    def tick_ai(self, track_id: int, lap_count: int) -> int:
        """Run one AI step; returns input_bits. Mirrors cpu_control()
        from main.c:5159-5277 line-for-line:
          - target = waypoints[next_waypoint] (or operator-uploaded real WP)
          - tr = atan2(tx-x, tz-z), normalized to (-180, 180]
          - rot_dif = ry - tr; reverse = !( -180 <= rot_dif < 180 )
          - if |rot_dif| > 6 deg: steer per (reverse, ry vs tr) sign table
          - cpu_gas = true (always)
          - cpu_action edge-triggered when current_powerup > 0
          - cpu_siren = true (harmless; only cop car uses it visually)
        """
        self.frame += 1
        wps = _get_track_waypoints(track_id)
        n = len(wps)
        if self.next_waypoint >= n:
            self.next_waypoint = 0
        tx, _ty, tz = wps[self.next_waypoint]

        # Heading toward waypoint, in degrees [-180, 180]. Matches the
        # jo_atan2f((tx - x), (tz - z)) call at main.c:5192 with the
        # same post-normalization at 5193-5196.
        target_deg = math.degrees(math.atan2(tx - self.x, tz - self.z))
        if target_deg > 180.0:
            target_deg -= 360.0
        elif target_deg <= -180.0:
            target_deg += 360.0

        # rot_dif = (ry - tr); reverse window matches main.c:5200-5206.
        rot_dif = (self.ry - target_deg)
        # Normalize rot_dif to (-180, 180] for the |rot_dif| <= 6 test.
        # main.c uses raw subtraction and relies on int16 wrap; we
        # reproduce the same useful range explicitly.
        while rot_dif > 180.0:
            rot_dif -= 360.0
        while rot_dif <= -180.0:
            rot_dif += 360.0
        reverse = not (-180.0 <= rot_dif < 180.0)

        bits = 0
        # cpu_gas = true (main.c:5241). Unconditional unless enable_controls
        # is false; on the server bots always have controls enabled.
        bits |= MNET_INPUT_GAS

        # Steering - mirrors the (reverse, ry vs tr) sign table at
        # main.c:5208-5238. Outside the 6-deg deadband, pick left vs right
        # to close on the target heading.
        if abs(rot_dif) > self.TURN_DEADBAND:
            if not reverse:
                if self.ry < target_deg:
                    bits |= MNET_INPUT_RIGHT
                elif self.ry > target_deg:
                    bits |= MNET_INPUT_LEFT
            else:
                if self.ry < target_deg:
                    bits |= MNET_INPUT_LEFT
                elif self.ry > target_deg:
                    bits |= MNET_INPUT_RIGHT

        # Powerup activation - edge-triggered, mirrors main.c:5243-5254.
        # Fire the action bit ONCE when a powerup becomes available, then
        # arm cpu_pressed_action so we don't spam it every frame. Clear the
        # latch when current_powerup goes back to 0 (powerup consumed).
        if self.current_powerup > 0 and not self.cpu_pressed_action:
            bits |= MNET_INPUT_ACTION
            self.cpu_pressed_action = True
        elif self.current_powerup == 0:
            self.cpu_pressed_action = False

        # cpu_siren = true (main.c:5259). Only car_selection==4 (cop car)
        # actually plays a siren; harmless on the wire either way. We map
        # it to the HORN bit, which is the closest analogue in the input
        # bitfield and is also what local cop cars trigger.
        bits |= MNET_INPUT_HORN

        return bits

    def step_physics(self, bits: int):
        """Cheap forward integration so server has a position to sync with."""
        # Steer
        if bits & MNET_INPUT_LEFT:
            self.ry -= 6
        if bits & MNET_INPUT_RIGHT:
            self.ry += 6
        # Wrap heading to int16 sane range
        while self.ry > 180:
            self.ry -= 360
        while self.ry <= -180:
            self.ry += 360

        # Speed
        if bits & MNET_INPUT_GAS:
            self.speed = min(self.speed + 2, self.BOT_SPEED)
        elif bits & MNET_INPUT_BRAKE:
            self.speed = max(self.speed - 4, 0)
        else:
            self.speed = max(self.speed - 1, 0)

        rad = math.radians(self.ry)
        self.x = s16(self.x + int(self.speed * math.sin(rad)))
        self.z = s16(self.z + int(self.speed * math.cos(rad)))

        # Waypoint progression
        wps = _get_track_waypoints(0)  # track is bound at start; we look up dynamically below
        # NOTE: tick_ai uses the right track; for distance check use whatever
        # is set on the simulation (caller will replace dist_to_next_waypoint).

    def update_waypoint(self, track_id: int, lap_count: int) -> bool:
        """Advance waypoint when within radius. Returns True if a lap completed."""
        wps = _get_track_waypoints(track_id)
        n = len(wps)
        tx, _ty, tz = wps[self.next_waypoint]
        dx = tx - self.x
        dz = tz - self.z
        d = int(math.sqrt(dx * dx + dz * dz))
        self.dist_to_next_waypoint = u16(d)
        self.current_waypoint = self.next_waypoint
        if d <= self.WAYPOINT_RADIUS:
            prev = self.next_waypoint
            self.next_waypoint = (self.next_waypoint + 1) % n
            # Wrap from last back to 0 == lap complete
            if self.next_waypoint == 1 and prev == 0:
                # noop — we just started
                pass
            if prev == n - 1 and self.next_waypoint == 0:
                # We crossed the line going from last waypoint back to 0;
                # treat as checkpoint advance only
                self.current_checkpoint = 0
                self.lap += 1
                return True
            else:
                self.current_checkpoint = self.next_waypoint
        return False


# ==========================================================================
# Game Simulation
# ==========================================================================


class GameSimulation:
    """Server-authoritative race state. Owns powerup roll, lap validation,
    finish detection, and bot stepping."""

    TICK_RATE = 20
    TICK_RATIO = 3  # 60fps Saturn / 20Hz server

    def __init__(self, track_id: int, seed: int, lap_count: int):
        self.track_id = track_id
        self.seed = seed & 0xFFFFFFFF
        self.lap_count = lap_count
        self.players = {}  # pid -> Player
        self.bots = []  # subset of players (also in self.players)
        self.match_started_at = time.time()
        self.race_finished = False
        self.winner_id = None  # set on RACE_FINISH

        # Per-slot powerup state.
        self.powerup_state = []
        for _ in range(POWERUP_SLOTS):
            self.powerup_state.append({
                "type": 0,        # 1..7 once spawned
                "active": False,  # currently on track
                "respawn_at": 0.0,  # wall-clock for re-spawn (0 = N/A)
                "x": 0, "y": 0, "z": 0,
            })

        self._tick = 0
        self._sync_round_robin = 0  # which pid index gets PLAYER_SYNC this tick

        # Per-pid RNG for the powerup-type roll, seeded from `seed` so that
        # given the same seed, the same powerup-spawn type sequence emerges.
        self._powerup_rng = random.Random(self.seed)

    def add_player(self, p: Player):
        self.players[p.player_id] = p
        if p.is_bot:
            self.bots.append(p)

    def initial_powerup_spawn(self) -> list:
        """Roll initial type for each of 8 slots. Returns events to broadcast."""
        events = []
        for slot in range(POWERUP_SLOTS):
            ptype = self._powerup_rng.randint(POWERUP_TYPE_MIN, POWERUP_TYPE_MAX)
            st = self.powerup_state[slot]
            st["type"] = ptype
            st["active"] = True
            st["respawn_at"] = 0.0
            # Position is a static placeholder (0,0,0) — the Saturn loads
            # real positions from the track .bin. The server is authoritative
            # for slot identity + type.
            st["x"], st["y"], st["z"] = 0, 0, 0
            events.append(("powerup_spawn", slot, ptype, 0, 0, 0))
        return events

    def reset_player_for_race(self, p: Player):
        p.lap = 0
        p.current_checkpoint = 0
        p.current_waypoint = 0
        p.dist_to_next_waypoint = 0
        p.position = 0
        p.start_time = time.time()
        p.current_time = 0.0
        p.best_lap_time = 0
        p.points = 0
        p.dnf = False
        p.finished = False
        p.finish_time = 0.0

    def handle_player_state(self, pid: int, x: int, y: int, z: int,
                            ry: int, speed: int,
                            lap: int, cp: int, cur_wp: int, dist_wp: int):
        p = self.players.get(pid)
        if not p:
            return
        p.x = x
        p.y = y
        p.z = z
        p.ry = ry
        p.speed = speed
        # Trust client for own current_waypoint/dist (rendering smoothness),
        # but DON'T trust client lap unless validated through LAP_COMPLETE.
        p.current_waypoint = cur_wp
        p.dist_to_next_waypoint = dist_wp
        p.last_state_recv_time = time.time()

    def handle_lap_complete(self, pid: int, lap: int, lap_time: int) -> tuple:
        """Validate a LAP_COMPLETE advisory.
        Returns (accepted, lap_notify_payload_or_none, finished_this_call).
        """
        p = self.players.get(pid)
        if not p:
            return (False, None, False)
        if p.dnf or p.finished:
            return (False, None, False)
        # Validate: lap must increase by exactly 1 from current
        if lap != p.lap + 1:
            log.warning("LAP_COMPLETE rejected from pid=%d: claimed lap=%d, current=%d",
                        pid, lap, p.lap)
            return (False, None, False)
        p.lap = lap
        p.current_checkpoint = 0
        if lap_time > 0 and (p.best_lap_time == 0 or lap_time < p.best_lap_time):
            p.best_lap_time = lap_time
        finished = False
        if p.lap >= self.lap_count:
            p.finished = True
            p.finish_time = time.time()
            finished = True
        return (True, (pid, lap, p.position), finished)

    def handle_powerup_pickup(self, pid: int, slot: int) -> tuple:
        """Server-validates a pickup. Returns (event_or_none, accepted)."""
        if slot < 0 or slot >= POWERUP_SLOTS:
            return (None, False)
        if pid not in self.players:
            return (None, False)
        st = self.powerup_state[slot]
        if not st["active"]:
            return (None, False)
        # Mark taken; schedule respawn.
        st["active"] = False
        st["respawn_at"] = time.time() + self._respawn_secs
        return (("powerup_destroy", slot, pid), True)

    @property
    def _respawn_secs(self) -> float:
        # Allow runtime override via attribute set by server tunables.
        return getattr(self, "_powerup_respawn_secs_override",
                       POWERUP_RESPAWN_DEFAULT)

    def tick_powerup_respawns(self) -> list:
        events = []
        now = time.time()
        for slot in range(POWERUP_SLOTS):
            st = self.powerup_state[slot]
            if not st["active"] and st["respawn_at"] > 0 and now >= st["respawn_at"]:
                ptype = self._powerup_rng.randint(POWERUP_TYPE_MIN, POWERUP_TYPE_MAX)
                st["type"] = ptype
                st["active"] = True
                st["respawn_at"] = 0.0
                events.append(("powerup_spawn", slot, ptype,
                               st["x"], st["y"], st["z"]))
        return events

    def recompute_positions(self):
        """Mirror set_player_position(): rank by lap > checkpoint > -dist_wp.
        Lower position number = better (1=first).
        DNF/disconnected players sort last among non-finishers."""
        ranked = list(self.players.values())
        # Higher lap is better; higher checkpoint is better;
        # SHORTER dist_to_next_waypoint is better, so we negate it.
        # Finished players sort by finish_time (earlier = better).
        def key(p):
            if p.finished:
                # Finished players ranked among themselves by finish_time ascending.
                return (3, -p.finish_time)
            if p.dnf:
                return (0, 0)
            return (2, p.lap, p.current_checkpoint, -p.dist_to_next_waypoint)
        ranked.sort(key=key, reverse=True)
        for i, p in enumerate(ranked):
            p.position = i + 1

    def tick_bots(self) -> list:
        """Step each bot TICK_RATIO sub-frames. Returns list of input-relay
        events: ('input_relay', pid, frame_num, bits). Also updates internal
        physics + lap tracking."""
        events = []
        for bot in self.bots:
            if bot.dnf or bot.finished:
                continue
            bits = bot.tick_ai(self.track_id, self.lap_count)
            for _ in range(self.TICK_RATIO):
                bot.step_physics(bits)
            lap_done = bot.update_waypoint(self.track_id, self.lap_count)
            if lap_done and bot.lap >= self.lap_count:
                bot.finished = True
                bot.finish_time = time.time()
            # Relay input only on change OR every ~15 sub-frames keepalive.
            bot.relay_force_counter += 1
            if bits != bot.last_relay_bits or bot.relay_force_counter >= 15:
                events.append(("input_relay", bot.player_id,
                               bot.frame & 0xFFFF, bits))
                bot.last_relay_bits = bits
                bot.relay_force_counter = 0
        return events

    def check_race_finish(self) -> tuple:
        """Returns (winner_id, standings) or (None, None) if not finished.
        Race is over when:
          - any human reaches lap_count (cross line on final lap), OR
          - all non-DNF, non-finished players are bots (humans all DNF/done)
        """
        if self.race_finished:
            return (None, None)

        humans = [p for p in self.players.values() if not p.is_bot]
        non_terminal = [p for p in self.players.values()
                        if not p.dnf and not p.finished]

        # Race ends when any human finishes, OR when no non-terminal players remain.
        any_human_finished = any(p.finished for p in humans)
        if not any_human_finished and non_terminal:
            return (None, None)

        # Build standings: position-sorted (already updated by recompute_positions)
        self.recompute_positions()
        standings = sorted(self.players.values(), key=lambda p: p.position)
        winner_id = None
        for p in standings:
            if p.position == 1:
                winner_id = p.player_id
                break

        # Award points + total time
        result = []
        for p in standings:
            pos = p.position
            pts = POINTS_TABLE[pos] if 1 <= pos < len(POINTS_TABLE) else 0
            p.points = pts
            p.total_points += pts
            total_time = int(p.finish_time - self.match_started_at) \
                if p.finish_time else int(time.time() - self.match_started_at)
            result.append((p.player_id, pos, total_time))

        self.race_finished = True
        self.winner_id = winner_id
        return (winner_id, result)


# ==========================================================================
# Client Info
# ==========================================================================


class MMMConnection:
    """Per-bridge TCP connection. Tracks auth state + lobby roster entry."""

    def __init__(self, sock: socket.socket, address: tuple):
        self.socket = sock
        self.address = address
        self.uuid = ""
        self.username = ""
        self.user_id = 0
        self.authenticated = False
        self.recv_buffer = b""
        self.last_activity = time.time()

        # Lobby/game state for the primary player on this connection
        self.ready = False
        self.in_game = False
        self.car_id = 0
        self.game_player_id = 0  # assigned at GAME_START
        self.local_player_ids = []  # additional pids on the same Saturn
        self.local_player_names = []
        self.local_player_cars = []

        # Egress telemetry
        self._egress_bytes = 0
        self._egress_window_start = time.time()
        self._egress_rate = 0

    def send_raw(self, data: bytes) -> bool:
        try:
            self.socket.sendall(data)
        except OSError:
            return False
        self._egress_bytes += len(data)
        now = time.time()
        elapsed = now - self._egress_window_start
        if elapsed >= 1.0:
            self._egress_rate = int(self._egress_bytes / elapsed)
            self._egress_bytes = 0
            self._egress_window_start = now
        return True


# ==========================================================================
# Leaderboard
# ==========================================================================


class Leaderboard:
    """Persistent JSON leaderboard.
    Schema: {name: {wins, best_lap_per_track: {track_id_str: time}, podiums, races, points}}
    """

    def __init__(self, path: str):
        self.path = path
        self.data = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.data = json.load(f).get("players", {})
                log.info("Loaded leaderboard: %d players", len(self.data))
            except Exception as e:
                log.warning("Failed to load leaderboard: %s", e)
                self.data = {}

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump({"players": self.data}, f, indent=2)
        except Exception as e:
            log.warning("Failed to save leaderboard: %s", e)

    def _entry(self, name: str) -> dict:
        if name not in self.data:
            self.data[name] = {
                "wins": 0,
                "best_lap_per_track": {},
                "podiums": 0,
                "races": 0,
                "points": 0,
            }
        return self.data[name]

    def update_after_race(self, track_id: int, standings: list,
                          name_for_pid: dict, best_laps: dict):
        """standings: list of (pid, position, total_time).
        name_for_pid: pid -> participant name.
        best_laps: pid -> best_lap_time_secs (0 if unset).
        """
        for pid, pos, _total in standings:
            name = name_for_pid.get(pid)
            if not name:
                continue
            e = self._entry(name)
            e["races"] += 1
            if pos == 1:
                e["wins"] += 1
            if pos <= 3:
                e["podiums"] += 1
            if 1 <= pos < len(POINTS_TABLE):
                e["points"] += POINTS_TABLE[pos]
            bl = best_laps.get(pid, 0)
            if bl > 0:
                key = str(track_id)
                cur = e["best_lap_per_track"].get(key, 0)
                if cur == 0 or bl < cur:
                    e["best_lap_per_track"][key] = bl
        self._save()

    def top_entries(self, limit: int = 10) -> list:
        """Returns sorted entries flattened for LEADERBOARD_DATA."""
        out = []
        for name, d in self.data.items():
            best_lap = 0
            for v in d.get("best_lap_per_track", {}).values():
                if v > 0 and (best_lap == 0 or v < best_lap):
                    best_lap = v
            out.append({
                "name": name,
                "wins": d.get("wins", 0),
                "best_lap": best_lap,
                "podiums": d.get("podiums", 0),
                "races": d.get("races", 0),
                "points": d.get("points", 0),
            })
        # Sort by points desc, then wins desc.
        out.sort(key=lambda e: (e["points"], e["wins"]), reverse=True)
        return out[:limit]


# ==========================================================================
# MMM Server
# ==========================================================================


class MMMServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 4826,
                 num_bots: int = 0, admin_port: int = 0,
                 admin_user: str = "admin", admin_password: str = "mmm2026"):
        self.host = host
        self.port = port
        self.clients = {}  # sock -> MMMConnection
        self.uuid_map = {}  # uuid -> username (persistent reconnect)
        self.server_socket = None
        self._running = False

        self.pending_auth = {}  # sock -> {deadline, buf, address}
        self.authenticated_bridges = set()

        # Game state
        self.game_active = False
        self.sim = None  # GameSimulation
        self._race_finish_announced_at = 0.0  # for WINNER banner timing

        # Bots: pre-instantiated BotPlayer (player_id assigned at GAME_START).
        self.bots = []
        for i in range(num_bots):
            name = BOT_NAMES[i % len(BOT_NAMES)]
            # Player ID 0xFE is sentinel; reassigned during start_game.
            self.bots.append(BotPlayer(0xFE, name, i))

        # Client-log writer (singleton). Side-effect: also expose globally
        # so non-method helpers (e.g. tests) can reach it if needed.
        global client_log_writer, join_history_writer
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if client_log_writer is None:
            client_log_writer = ClientLogWriter(base_dir)
        self.client_log = client_log_writer
        if join_history_writer is None:
            join_history_writer = JoinHistoryWriter(base_dir)
        self.join_history = join_history_writer

        # Leaderboard
        lb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "mmm_leaderboard.json")
        self.leaderboard = Leaderboard(lb_path)

        # Tick timing
        self._last_tick = 0.0
        self._tick_interval = 1.0 / GameSimulation.TICK_RATE
        self._tick_counter = 0

        # Admin portal
        self._admin_port = admin_port
        self._admin_user = admin_user
        self._admin_password = admin_password
        self._admin_command_queue = queue.Queue()
        self._admin_httpd = None
        self._admin_thread = None
        self._start_time = time.time()

        # Tunables
        self.tuning = {
            "bot_count": num_bots,
            "powerup_respawn_secs": int(POWERUP_RESPAWN_DEFAULT),
            "lap_count": DEFAULT_LAP_COUNT,
            "random_track": True,
            "forced_track_id": 1,
        }

        # Delta input relay state
        self.last_relayed_input = {}
        self.relay_cooldown = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(MAX_BRIDGES + 5)
        self.server_socket.setblocking(False)

        log.info("MMM Server listening on %s:%d", self.host, self.port)
        self._running = True
        self._start_admin_server()
        self._run()

    def _run(self):
        while self._running:
            read_sockets = [self.server_socket]
            read_sockets.extend(self.pending_auth.keys())
            read_sockets.extend(self.clients.keys())

            timeout = self._tick_interval if self.game_active else 1.0
            try:
                readable, _, _ = select.select(read_sockets, [], [], timeout)
            except (ValueError, OSError):
                self._cleanup_dead_sockets()
                continue

            now = time.time()
            for sock in readable:
                if sock is self.server_socket:
                    self._accept_connection()
                elif sock in self.pending_auth:
                    self._handle_bridge_auth(sock, now)
                elif sock in self.clients:
                    self._handle_client_data(sock)

            if self.game_active and self.sim:
                if now - self._last_tick >= self._tick_interval:
                    self._last_tick = now
                    self._game_tick()

            self._check_timeouts(now)
            self._process_admin_commands()

    # ------------------------------------------------------------------
    # Connection accept + bridge auth
    # ------------------------------------------------------------------

    def _accept_connection(self):
        try:
            client_sock, addr = self.server_socket.accept()
            client_sock.setblocking(False)
        except OSError:
            return
        if len(self.authenticated_bridges) >= MAX_BRIDGES:
            log.warning("Max bridges reached, rejecting %s:%d", addr[0], addr[1])
            client_sock.close()
            return
        log.info("New connection from %s:%d", addr[0], addr[1])
        self.pending_auth[client_sock] = {
            "deadline": time.time() + AUTH_TIMEOUT,
            "buf": b"",
            "address": addr,
        }

    def _handle_bridge_auth(self, sock, now: float):
        info = self.pending_auth[sock]
        if now > info["deadline"]:
            log.warning("Auth timeout from %s", info["address"])
            self._record_join_event("auth_fail", ip=info["address"][0],
                                    reason="timeout")
            sock.close()
            del self.pending_auth[sock]
            return
        try:
            data = sock.recv(256)
        except (BlockingIOError, OSError):
            return
        if not data:
            sock.close()
            del self.pending_auth[sock]
            return
        info["buf"] += data
        buf = info["buf"]
        magic_len = len(AUTH_MAGIC)
        if len(buf) < magic_len:
            return
        if buf[:magic_len] != AUTH_MAGIC:
            log.warning("Invalid auth magic from %s", info["address"])
            self._record_join_event("auth_fail", ip=info["address"][0],
                                    reason="bad magic")
            sock.close()
            del self.pending_auth[sock]
            return
        if len(buf) < magic_len + 1:
            return
        secret_len = buf[magic_len]
        total_needed = magic_len + 1 + secret_len
        if len(buf) < total_needed:
            return
        received_secret = buf[magic_len + 1:total_needed]
        if received_secret != SHARED_SECRET:
            log.warning("Wrong shared secret from %s", info["address"])
            self._record_join_event("auth_fail", ip=info["address"][0],
                                    reason="wrong secret")
            sock.close()
            del self.pending_auth[sock]
            return
        try:
            sock.sendall(bytes([AUTH_OK]))
        except OSError:
            sock.close()
            del self.pending_auth[sock]
            return
        log.info("Bridge authenticated from %s:%d",
                 info["address"][0], info["address"][1])
        self._record_join_event("auth_ok", ip=info["address"][0],
                                reason="bridge auth")
        self.authenticated_bridges.add(sock)
        del self.pending_auth[sock]
        client = MMMConnection(sock, info["address"])
        self.clients[sock] = client

    # ------------------------------------------------------------------
    # SNCP frame parser + dispatch
    # ------------------------------------------------------------------

    def _handle_client_data(self, sock):
        client = self.clients.get(sock)
        if not client:
            return
        try:
            data = sock.recv(MAX_RECV_BUFFER)
        except (BlockingIOError, OSError):
            return
        if not data:
            self._remove_client(sock, "connection closed")
            return
        client.last_activity = time.time()
        client.recv_buffer += data
        while len(client.recv_buffer) >= 2:
            payload_len = (client.recv_buffer[0] << 8) | client.recv_buffer[1]
            total = 2 + payload_len
            if payload_len == 0 or payload_len > MAX_RECV_BUFFER:
                log.warning("Invalid frame from %s, disconnecting", client.address)
                self._remove_client(sock, "invalid frame")
                return
            if len(client.recv_buffer) < total:
                break
            payload = client.recv_buffer[2:total]
            client.recv_buffer = client.recv_buffer[total:]
            self._process_message(sock, client, payload)

    def _process_message(self, sock, client: MMMConnection, payload: bytes):
        if not payload:
            return
        msg_type = payload[0]
        if msg_type == MSG_CONNECT:
            self._handle_connect(sock, client, payload)
        elif msg_type == MSG_SET_USERNAME:
            self._handle_set_username(sock, client, payload)
        elif msg_type == MSG_HEARTBEAT or msg_type == MNET_MSG_HEARTBEAT_GAME:
            pass
        elif msg_type == MSG_DISCONNECT:
            self._remove_client(sock, "disconnect requested")
        elif msg_type == MNET_MSG_READY:
            self._handle_ready(sock, client)
        elif msg_type == MNET_MSG_START_GAME_REQ:
            self._handle_start_game(sock, client)
        elif msg_type == MNET_MSG_INPUT_STATE:
            self._handle_input_state(sock, client, payload)
        elif msg_type == MNET_MSG_PLAYER_STATE:
            self._handle_player_state(sock, client, payload)
        elif msg_type == MNET_MSG_POWERUP_PICKUP:
            self._handle_powerup_pickup(sock, client, payload)
        elif msg_type == MNET_MSG_LAP_COMPLETE:
            self._handle_lap_complete(sock, client, payload)
        elif msg_type == MNET_MSG_CAR_SELECT:
            self._handle_car_select(sock, client, payload)
        elif msg_type == MNET_MSG_ADD_LOCAL_PLAYER:
            self._handle_add_local_player(sock, client, payload)
        elif msg_type == MNET_MSG_REMOVE_LOCAL_PLAYER:
            self._handle_remove_local_player(sock, client)
        elif msg_type == MNET_MSG_LEADERBOARD_REQ:
            self._send_leaderboard_to_client(client)
        elif msg_type == MNET_MSG_CLIENT_LOG:
            self._handle_client_log(sock, client, payload)
        else:
            log.debug("Unknown message type 0x%02X from %s",
                      msg_type, client.address)

    # ------------------------------------------------------------------
    # Client diagnostic log (CLIENT_LOG opcode)
    # ------------------------------------------------------------------

    def _handle_client_log(self, sock, client: MMMConnection, payload: bytes):
        # Wire layout: [type:1][level:1][text_len:1][text:N]
        if len(payload) < 3:
            return
        level = payload[1]
        tlen = payload[2]
        if len(payload) < 3 + tlen:
            return
        try:
            text = payload[3:3 + tlen].decode("utf-8", errors="replace")
        except Exception:
            return
        # Resolve auth state for the connection. Pre-auth logs are accepted
        # too (helpful for "could not even AUTH" diagnostics) but with
        # placeholder identity.
        pid = client.user_id if client.authenticated else None
        name = client.username if client.authenticated else "(unauth)"
        self.client_log.write(level, pid, name, text)

    # ------------------------------------------------------------------
    # SNCP auth handlers
    # ------------------------------------------------------------------

    def _handle_connect(self, sock, client: MMMConnection, payload: bytes):
        client_uuid = ""
        if len(payload) >= 1 + UUID_LEN:
            client_uuid = payload[1:1 + UUID_LEN].decode(
                "ascii", errors="replace").rstrip("\x00")
        if client_uuid and client_uuid in self.uuid_map:
            client.uuid = client_uuid
            client.username = self.uuid_map[client_uuid]
            client.user_id = self._next_user_id()
            client.authenticated = True
            log.info("Player reconnected: %s (uuid=%s..)",
                     client.username, client_uuid[:8])
            client.send_raw(build_welcome_back(
                client.user_id, client.uuid, client.username))
            self._record_join_event("connect", name=client.username,
                                    ip=client.address[0],
                                    player_id=client.user_id,
                                    reason="reconnect")
            self._broadcast_lobby_state()
            self._send_leaderboard_to_client(client)
        else:
            if not client.uuid:
                client.uuid = str(uuid.uuid4())
                client.user_id = self._next_user_id()
                self.uuid_map[client.uuid] = ""
            log.info("New player connected (uuid=%s..)", client.uuid[:8])
            client.send_raw(build_username_required())

    def _handle_set_username(self, sock, client: MMMConnection, payload: bytes):
        if len(payload) < 2:
            return
        name_len = payload[1]
        if len(payload) < 2 + name_len:
            return
        username = payload[2:2 + name_len].decode("utf-8", errors="replace")
        username = username[:USERNAME_MAX_LEN].strip()
        if not username:
            client.send_raw(build_username_taken())
            return
        for s, c in self.clients.items():
            if s != sock and c.authenticated and \
               c.username.lower() == username.lower():
                client.send_raw(build_username_taken())
                return
        # Lobby capacity check (humans + their locals + bots <= MAX_PLAYERS)
        slots = 0
        for c in self.clients.values():
            if c.authenticated:
                slots += 1 + len(c.local_player_names)
        slots += len(self.bots)
        if slots >= MAX_PLAYERS:
            client.send_raw(build_log("SERVER FULL (%d/%d)" %
                                      (slots, MAX_PLAYERS)))
            return
        client.username = username
        client.authenticated = True
        self.uuid_map[client.uuid] = username
        log.info("Player %d set username: %s", client.user_id, username)
        self._record_join_event("connect", name=username,
                                ip=client.address[0],
                                player_id=client.user_id,
                                reason="new")
        client.send_raw(build_welcome(client.user_id, client.uuid, username))
        if self.game_active:
            client.send_raw(build_log("RACE IN PROGRESS - WAIT FOR NEXT ROUND"))
        self._broadcast_lobby_state()
        self._send_leaderboard_to_client(client)
        for s, c in self.clients.items():
            if s != sock and c.authenticated:
                c.send_raw(build_player_join(client.user_id, username))
                c.send_raw(build_log("%s JOINED!" % username.upper()))

    def _handle_add_local_player(self, sock, client: MMMConnection,
                                 payload: bytes):
        if not client.authenticated:
            return
        if len(payload) < 2:
            return
        name_len = payload[1]
        if len(payload) < 2 + name_len:
            return
        name = payload[2:2 + name_len].decode("utf-8", errors="replace")
        name = name[:USERNAME_MAX_LEN].strip()
        if not name:
            return
        # MMM: max 4 racers; one Saturn can host 1 primary + 1 local-coop slot.
        if client.local_player_names:
            return
        # Capacity guard
        slots = 0
        for c in self.clients.values():
            if c.authenticated:
                slots += 1 + len(c.local_player_names)
        slots += len(self.bots)
        if slots >= MAX_PLAYERS:
            client.send_raw(build_log("SERVER FULL"))
            return
        # Disambiguate
        all_names = set()
        for c in self.clients.values():
            if c.authenticated:
                all_names.add(c.username.lower())
                for ln in c.local_player_names:
                    all_names.add(ln.lower())
        for bot in self.bots:
            all_names.add(bot.name.lower())
        if name.lower() in all_names:
            for suffix in range(2, 10):
                candidate = name + str(suffix)
                if candidate.lower() not in all_names:
                    name = candidate
                    break
        client.local_player_names.append(name)
        client.local_player_cars.append(0)
        # Lobby-time slot reservation: do NOT send LOCAL_PLAYER_ACK here —
        # the actual pid isn't known yet. Real ACK with pid is sent at
        # GAME_START time (see _start_match around line 1726). The lobby
        # state broadcast below is the authoritative roster update.
        log.info("Player %s added local-coop slot: %s", client.username, name)
        self._broadcast_lobby_state()

    def _handle_remove_local_player(self, sock, client: MMMConnection):
        if not client.authenticated:
            return
        if not client.local_player_names:
            return
        removed = client.local_player_names.pop()
        if client.local_player_cars:
            client.local_player_cars.pop()
        log.info("Player %s removed local-coop slot: %s",
                 client.username, removed)
        if self.game_active and self.sim and client.local_player_ids:
            pid = client.local_player_ids.pop()
            p = self.sim.players.get(pid)
            if p:
                p.dnf = True
            self._broadcast_to_game(build_player_leave(pid))
        elif client.local_player_ids:
            client.local_player_ids.pop()
        self._broadcast_lobby_state()

    def _handle_car_select(self, sock, client: MMMConnection, payload: bytes):
        if not client.authenticated:
            return
        if len(payload) < 2:
            return
        car_id = payload[1]
        # If client provides extra byte, treat second byte as local-pid
        # selector (0=primary, 1=second local). For now: primary only.
        client.car_id = car_id
        self._broadcast_lobby_state()

    def _handle_ready(self, sock, client: MMMConnection):
        if not client.authenticated:
            return
        if self.game_active:
            return
        client.ready = not client.ready
        log.info("Player %s ready=%s", client.username, client.ready)
        self._broadcast_lobby_state()

    # ------------------------------------------------------------------
    # Game start
    # ------------------------------------------------------------------

    def _handle_start_game(self, sock, client: MMMConnection):
        if self.game_active:
            return
        if not client.authenticated:
            return

        ready_clients = [c for c in self.clients.values()
                         if c.authenticated and c.ready]
        # Total slots = ready primaries + their locals + bots
        total = len(ready_clients)
        for c in ready_clients:
            total += len(c.local_player_names)
        total += len(self.bots)

        if total < MIN_PLAYERS:
            client.send_raw(build_log("NEED %d+ READY PLAYERS" % MIN_PLAYERS))
            return
        if total > MAX_PLAYERS:
            client.send_raw(build_log("TOO MANY PLAYERS (MAX %d)" %
                                      MAX_PLAYERS))
            return

        # Pick track + seed
        if self.tuning["random_track"]:
            track_id = random.randint(TRACK_MIN, TRACK_MAX)
        else:
            track_id = self.tuning["forced_track_id"]
            if track_id < TRACK_MIN or track_id > TRACK_MAX:
                track_id = TRACK_MIN
        seed = random.randint(0, 0xFFFFFFFF)
        lap_count = self.tuning["lap_count"]

        log.info("Race starting! Track=%d, seed=%08X, %d slots, laps=%d",
                 track_id, seed, total, lap_count)

        self.sim = GameSimulation(track_id, seed, lap_count)
        # Apply runtime respawn override
        self.sim._powerup_respawn_secs_override = float(
            self.tuning["powerup_respawn_secs"])
        self.game_active = True
        self._last_tick = time.time()
        self._tick_counter = 0
        self.last_relayed_input.clear()
        self.relay_cooldown.clear()

        pid = 0
        # Primaries
        for c in ready_clients:
            c.in_game = True
            c.game_player_id = pid
            c.local_player_ids = []
            p = Player(pid, c.username, c.car_id, is_bot=False, is_local_p2=False)
            self.sim.add_player(p)
            self.sim.reset_player_for_race(p)
            pid += 1
        # Local-coop
        for c in ready_clients:
            for i, lname in enumerate(c.local_player_names):
                car = c.local_player_cars[i] if i < len(c.local_player_cars) else 0
                c.local_player_ids.append(pid)
                p = Player(pid, lname, car, is_bot=False, is_local_p2=True)
                self.sim.add_player(p)
                self.sim.reset_player_for_race(p)
                pid += 1
        # Bots
        for bot in self.bots:
            bot.player_id = pid
            bot.reset_for_track(track_id)
            self.sim.add_player(bot)
            self.sim.reset_player_for_race(bot)
            pid += 1

        # Send GAME_START + LOCAL_PLAYER_ACK to each connection
        for c in ready_clients:
            opp = total - 1
            c.send_raw(build_game_start(seed, c.game_player_id, opp,
                                        track_id, lap_count))
            for lp_id in c.local_player_ids:
                c.send_raw(build_local_player_ack(lp_id))

        # Roster: send PLAYER_JOIN to each in-game client so they know id->name.
        roster = []
        for c in ready_clients:
            roster.append((c.game_player_id, c.username))
        for c in ready_clients:
            for i, ln in enumerate(c.local_player_names):
                roster.append((c.local_player_ids[i], ln))
        for bot in self.bots:
            roster.append((bot.player_id, bot.name))
        for c in ready_clients:
            for rpid, rname in roster:
                c.send_raw(build_player_join(rpid, rname))

        # Spawn powerups
        for evt in self.sim.initial_powerup_spawn():
            self._broadcast_event(evt)

    # ------------------------------------------------------------------
    # In-game handlers
    # ------------------------------------------------------------------

    def _handle_input_state(self, sock, client: MMMConnection, payload: bytes):
        if not self.game_active or not client.in_game:
            return
        # P1 form: [type:1][frame:2][input:1] = 4 bytes
        # P2 form: [type:1][pid:1][frame:2][input:1] = 5 bytes
        if len(payload) >= 5:
            player_id = payload[1]
            frame_num = (payload[2] << 8) | payload[3]
            input_bits = payload[4]
            valid_ids = [client.game_player_id] + client.local_player_ids
            if player_id not in valid_ids:
                return
        elif len(payload) >= 4:
            player_id = client.game_player_id
            frame_num = (payload[1] << 8) | payload[2]
            input_bits = payload[3]
        else:
            return

        # Delta + keepalive every 15 frames
        last = self.last_relayed_input.get(player_id, -1)
        cooldown = self.relay_cooldown.get(player_id, 15)
        if input_bits != last or cooldown >= 15:
            relay_msg = build_input_relay(player_id, frame_num, input_bits)
            for s, c in self.clients.items():
                if c.in_game and s != sock:
                    c.send_raw(relay_msg)
            self.last_relayed_input[player_id] = input_bits
            self.relay_cooldown[player_id] = 0
        else:
            self.relay_cooldown[player_id] = cooldown + 1

        if self.sim:
            p = self.sim.players.get(player_id)
            if p:
                p.last_input_bits = input_bits
                p.last_input_frame = frame_num

    def _handle_player_state(self, sock, client: MMMConnection, payload: bytes):
        """PLAYER_STATE: 16-byte primary or 17-byte with explicit player_id."""
        if not self.game_active or not self.sim:
            return
        if not client.in_game:
            return
        # P1: [type][x:2][y:2][z:2][ry:2][speed:2][lap][cp][cur_wp][dist_wp:2] = 16 bytes
        # P2: [type][pid][x:2][y:2][z:2][ry:2][speed:2][lap][cp][cur_wp][dist_wp:2] = 17 bytes
        if len(payload) >= 17:
            player_id = payload[1]
            valid = [client.game_player_id] + client.local_player_ids
            if player_id not in valid:
                return
            off = 2
        elif len(payload) >= 16:
            player_id = client.game_player_id
            off = 1
        else:
            return
        try:
            x, y, z, ry, speed = struct.unpack(
                "!hhhhh", payload[off:off + 10])
            lap = payload[off + 10]
            cp = payload[off + 11]
            cur_wp = payload[off + 12]
            dist_wp = (payload[off + 13] << 8) | payload[off + 14]
        except (struct.error, IndexError):
            return
        self.sim.handle_player_state(player_id, x, y, z, ry, speed,
                                     lap, cp, cur_wp, dist_wp)

    def _handle_powerup_pickup(self, sock, client: MMMConnection,
                               payload: bytes):
        if not self.game_active or not self.sim:
            return
        if not client.in_game:
            return
        if len(payload) < 2:
            return
        slot = payload[1]
        # Server is authoritative for slot identity. Trust the connection's
        # claimed pid = primary unless extended form (3 bytes) says otherwise.
        if len(payload) >= 3:
            pid = payload[2]
            valid = [client.game_player_id] + client.local_player_ids
            if pid not in valid:
                return
        else:
            pid = client.game_player_id
        evt, ok = self.sim.handle_powerup_pickup(pid, slot)
        if ok and evt:
            self._broadcast_event(evt)

    def _handle_lap_complete(self, sock, client: MMMConnection,
                             payload: bytes):
        if not self.game_active or not self.sim:
            return
        if not client.in_game:
            return
        # P1: [type][lap][lap_time:2] = 4 bytes
        # P2: [type][pid][lap][lap_time:2] = 5 bytes
        if len(payload) >= 5:
            pid = payload[1]
            valid = [client.game_player_id] + client.local_player_ids
            if pid not in valid:
                return
            lap = payload[2]
            lap_time = (payload[3] << 8) | payload[4]
        elif len(payload) >= 4:
            pid = client.game_player_id
            lap = payload[1]
            lap_time = (payload[2] << 8) | payload[3]
        else:
            return
        accepted, notify, finished = self.sim.handle_lap_complete(
            pid, lap, lap_time)
        if accepted:
            self.sim.recompute_positions()
            p = self.sim.players.get(pid)
            pos = p.position if p else 0
            self._broadcast_to_game(build_lap_notify(pid, lap, pos))

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def _game_tick(self):
        if not self.sim:
            return
        self._tick_counter += 1

        # Bot AI + relay
        bot_events = self.sim.tick_bots()
        for evt in bot_events:
            self._broadcast_event(evt)

        # Powerup respawns
        respawn_events = self.sim.tick_powerup_respawns()
        for evt in respawn_events:
            self._broadcast_event(evt)

        # Recompute positions every 5 ticks (4Hz) — cheap, smooth enough
        if self._tick_counter % 5 == 0:
            self.sim.recompute_positions()

        # Round-robin PLAYER_SYNC: one slot per tick (at 20Hz, each player
        # gets a sync every players-count ticks; with 4 players => 5Hz).
        if self.sim.players:
            pids = sorted(self.sim.players.keys())
            slot = self.sim._sync_round_robin % len(pids)
            self.sim._sync_round_robin += 1
            target_pid = pids[slot]
            p = self.sim.players[target_pid]
            sync_msg = build_player_sync(p.player_id, p.x, p.y, p.z,
                                         p.ry, p.speed,
                                         p.lap, p.current_checkpoint,
                                         p.current_waypoint,
                                         p.dist_to_next_waypoint)
            self._broadcast_to_game(sync_msg)

        # Race finish detection
        winner_id, standings = self.sim.check_race_finish()
        if winner_id is not None and standings is not None:
            self._announce_race_finish(winner_id, standings)

    def _announce_race_finish(self, winner_id: int, standings: list):
        log.info("Race finished! Winner=%d, %d standings",
                 winner_id, len(standings))
        # Broadcast RACE_FINISH (full standings)
        self._broadcast_to_game(build_race_finish(winner_id, standings))
        # Also send GAME_OVER (older clients may key on this)
        self._broadcast_to_game(build_game_over(winner_id))

        # Update leaderboard
        if self.sim:
            name_for_pid = {}
            best_laps = {}
            for c in self.clients.values():
                if c.in_game:
                    name_for_pid[c.game_player_id] = c.username
                    p = self.sim.players.get(c.game_player_id)
                    best_laps[c.game_player_id] = p.best_lap_time if p else 0
                    for i, lp_id in enumerate(c.local_player_ids):
                        ln = c.local_player_names[i] \
                            if i < len(c.local_player_names) else "P2"
                        name_for_pid[lp_id] = ln
                        lp = self.sim.players.get(lp_id)
                        best_laps[lp_id] = lp.best_lap_time if lp else 0
            for bot in self.bots:
                name_for_pid[bot.player_id] = bot.name
                best_laps[bot.player_id] = bot.best_lap_time
            self.leaderboard.update_after_race(self.sim.track_id,
                                               standings, name_for_pid,
                                               best_laps)

        self._race_finish_announced_at = time.time()
        # WINNER banner timing: hold game state for ~5s before resetting
        # so clients can show the results screen, then return to lobby.
        # Actual reset happens lazily on next admin/timer event;
        # for now reset immediately since the Saturn-side handles its own
        # results screen via RACE_FINISH payload.
        self._reset_to_lobby()

    def _reset_to_lobby(self):
        """Reset match state & ready flags. Mirrors userver pattern of
        clearing ready (forces fresh A press for the next round)."""
        self.game_active = False
        self.sim = None
        for c in self.clients.values():
            c.in_game = False
            c.ready = False
            c.local_player_ids = []
        self._broadcast_lobby_state()
        self._broadcast_leaderboard()

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    def _broadcast_event(self, evt):
        kind = evt[0]
        if kind == "input_relay":
            _, pid, frame_num, bits = evt
            msg = build_input_relay(pid, frame_num, bits)
            self._broadcast_to_game(msg)
        elif kind == "powerup_spawn":
            _, slot, ptype, x, y, z = evt
            msg = build_powerup_spawn(slot, ptype, x, y, z)
            self._broadcast_to_game(msg)
        elif kind == "powerup_destroy":
            _, slot, taker_id = evt
            msg = build_powerup_destroy(slot, taker_id)
            self._broadcast_to_game(msg)

    def _broadcast_to_game(self, msg: bytes):
        for c in self.clients.values():
            if c.in_game:
                c.send_raw(msg)

    def _broadcast(self, msg: bytes):
        for c in self.clients.values():
            if c.authenticated:
                c.send_raw(msg)

    def _broadcast_lobby_state(self):
        players = []
        for c in self.clients.values():
            if c.authenticated:
                players.append({
                    "id": c.user_id,
                    "name": c.username,
                    "car": c.car_id,
                    "ready": c.ready,
                    "is_local": False,
                })
                for i, ln in enumerate(c.local_player_names):
                    players.append({
                        "id": c.user_id,
                        "name": ln,
                        "car": c.local_player_cars[i] if i < len(c.local_player_cars) else 0,
                        "ready": c.ready,
                        "is_local": True,
                    })
        for bot in self.bots:
            players.append({
                "id": 200 + bot.bot_idx,
                "name": bot.name,
                "car": bot.car_id,
                "ready": True,
                "is_local": False,
            })
        msg = build_lobby_state(players)
        self._broadcast(msg)

    def _broadcast_leaderboard(self):
        entries = self.leaderboard.top_entries(10)
        msg = build_leaderboard_data(entries)
        self._broadcast(msg)

    def _send_leaderboard_to_client(self, client: MMMConnection):
        entries = self.leaderboard.top_entries(10)
        client.send_raw(build_leaderboard_data(entries))

    # ------------------------------------------------------------------
    # User ID / cleanup / timeouts
    # ------------------------------------------------------------------

    def _next_user_id(self) -> int:
        used = {c.user_id for c in self.clients.values() if c.user_id > 0}
        uid = 1
        while uid in used:
            uid += 1
        return uid

    def _remove_client(self, sock, reason: str):
        client = self.clients.get(sock)
        if client:
            log.info("Removing %s (%s): %s",
                     client.username or "unknown", client.address, reason)
            # Classify event for the join-history log.
            if "kick" in reason.lower():
                ev = "kick"
            else:
                ev = "disconnect"
            if "timeout" in reason.lower():
                hist_reason = "timeout"
            elif "kick" in reason.lower():
                hist_reason = reason
            else:
                hist_reason = "clean"
            if client.authenticated:
                self._record_join_event(
                    ev, name=client.username or "",
                    ip=client.address[0],
                    player_id=client.user_id,
                    reason=hist_reason)
            if client.in_game and self.game_active and self.sim:
                # Mark all of this connection's pids as DNF (give them
                # last position via recompute_positions).
                pids = [client.game_player_id] + client.local_player_ids
                for pid in pids:
                    p = self.sim.players.get(pid)
                    if p:
                        p.dnf = True
                # Notify other in-game clients
                leave_msg = build_player_leave(client.user_id)
                log_msg = build_log("%s DISCONNECTED" %
                                    (client.username or "Player"))
                for s, c in self.clients.items():
                    if c.in_game and s != sock:
                        c.send_raw(leave_msg)
                        c.send_raw(log_msg)
                client.in_game = False
                client.ready = False
                # If no real players remain in the race, abort it.
                remaining = [c for c in self.clients.values()
                             if c.in_game and c is not client]
                if not remaining:
                    log.info("Last human left — ending race")
                    self._reset_to_lobby()
            elif client.in_game:
                client.in_game = False
                client.ready = False
            del self.clients[sock]

        self.authenticated_bridges.discard(sock)
        try:
            sock.close()
        except OSError:
            pass
        if not self.game_active:
            self._broadcast_lobby_state()

    def _cleanup_dead_sockets(self):
        dead = []
        for sock in list(self.pending_auth.keys()):
            try:
                sock.fileno()
            except OSError:
                dead.append(sock)
        for sock in dead:
            del self.pending_auth[sock]
        dead = []
        for sock in list(self.clients.keys()):
            try:
                sock.fileno()
            except OSError:
                dead.append(sock)
        for sock in dead:
            self._remove_client(sock, "dead socket")

    def _check_timeouts(self, now: float):
        expired = [s for s, info in self.pending_auth.items()
                   if now > info["deadline"]]
        for sock in expired:
            addr = self.pending_auth[sock]["address"]
            log.warning("Auth timeout for %s", addr)
            self._record_join_event("auth_fail", ip=addr[0],
                                    reason="timeout")
            sock.close()
            del self.pending_auth[sock]
        for sock in list(self.clients.keys()):
            client = self.clients[sock]
            if now - client.last_activity > HEARTBEAT_TIMEOUT:
                self._remove_client(sock, "heartbeat timeout")

    # ------------------------------------------------------------------
    # Admin portal
    # ------------------------------------------------------------------

    def _start_admin_server(self):
        if not self._admin_port:
            return
        handler_class = _make_mmm_admin_handler(self)
        try:
            self._admin_httpd = ThreadingHTTPServer(
                ("0.0.0.0", self._admin_port), handler_class)
            self._admin_httpd.daemon_threads = True
        except OSError as e:
            log.error("Failed to start admin server on port %d: %s",
                      self._admin_port, e)
            return
        self._admin_thread = threading.Thread(
            target=self._admin_httpd.serve_forever, daemon=True)
        self._admin_thread.start()
        log.info("Admin portal listening on http://0.0.0.0:%d/",
                 self._admin_port)

    def _process_admin_commands(self):
        while True:
            try:
                cmd = self._admin_command_queue.get_nowait()
            except queue.Empty:
                break
            action = cmd.get("cmd", "")
            if action == "kick":
                target_id = cmd.get("player_id", -1)
                for sock, info in list(self.clients.items()):
                    if info.user_id == target_id:
                        log.info("Admin kicked %s", info.username)
                        self._remove_client(sock, "kicked by admin")
                        break
            elif action == "force_end_race":
                if self.game_active and self.sim:
                    log.info("Admin forcing race end")
                    self.sim.recompute_positions()
                    standings = sorted(self.sim.players.values(),
                                       key=lambda p: p.position)
                    winner_id = standings[0].player_id if standings else 0xFF
                    result = []
                    for p in standings:
                        total_time = int(time.time() - self.sim.match_started_at)
                        result.append((p.player_id, p.position, total_time))
                    self._announce_race_finish(winner_id, result)
            elif action == "add_bot":
                if not self.game_active and len(self.bots) < MAX_PLAYERS - 1:
                    idx = len(self.bots)
                    name = BOT_NAMES[idx % len(BOT_NAMES)]
                    self.bots.append(BotPlayer(0xFE, name, idx))
                    log.info("Admin added bot %s", name)
                    self._broadcast_lobby_state()
            elif action == "remove_bot":
                if not self.game_active and self.bots:
                    removed = self.bots.pop()
                    log.info("Admin removed bot %s", removed.name)
                    self._broadcast_lobby_state()
            elif action == "tune":
                key = cmd.get("key")
                val = cmd.get("value")
                if key in self.tuning:
                    # Type-coerce based on existing default.
                    cur = self.tuning[key]
                    try:
                        if isinstance(cur, bool):
                            new_val = (str(val).lower() in ("true", "1", "yes", "on"))
                        elif isinstance(cur, int):
                            new_val = int(val)
                        else:
                            new_val = val
                    except (TypeError, ValueError):
                        log.warning("Tune: bad value for %s: %r", key, val)
                        continue
                    self.tuning[key] = new_val
                    log.info("Tuned %s = %r", key, new_val)
                    if self.sim and key == "powerup_respawn_secs":
                        self.sim._powerup_respawn_secs_override = float(new_val)

    def _record_join_event(self, event: str, name: str = "", ip: str = "",
                           player_id=None, reason: str = ""):
        """Best-effort wrapper around the join-history writer; never raises."""
        try:
            jh = getattr(self, "join_history", None)
            if jh is not None:
                jh.append(event, name=name, ip=ip,
                          player_id=player_id, reason=reason)
        except Exception as e:  # pragma: no cover — defensive
            log.warning("join_history append failed: %s", e)

    def _build_admin_state(self):
        now = time.time()
        players = []
        for sock, info in list(self.clients.items()):
            if not info.authenticated:
                continue
            status = "lobby"
            position = 0
            lap = 0
            if info.in_game and self.sim:
                p = self.sim.players.get(info.game_player_id)
                if p:
                    if p.dnf:
                        status = "DNF"
                    elif p.finished:
                        status = "finished"
                    else:
                        status = "racing"
                    position = p.position
                    lap = p.lap
            players.append({
                "user_id": info.user_id,
                "username": info.username,
                "uuid": info.uuid,
                "address": "%s:%d" % info.address,
                "status": status,
                "ready": info.ready,
                "car_id": info.car_id,
                "local_count": len(info.local_player_names),
                "lap": lap,
                "position": position,
                "idle": round(now - info.last_activity, 1),
                "egress_bps": getattr(info, "_egress_rate", 0),
            })
        bots = []
        for bot in self.bots:
            entry = {
                "name": bot.name,
                "bot_idx": bot.bot_idx,
                "lap": bot.lap,
                "position": bot.position,
            }
            bots.append(entry)
        match = {
            "active": self.game_active,
            "track_id": self.sim.track_id if self.sim else 0,
            "lap_count": self.sim.lap_count if self.sim else self.tuning["lap_count"],
            "winner_id": self.sim.winner_id if self.sim else None,
            "elapsed": round(time.time() - self.sim.match_started_at, 1)
                       if self.sim else 0.0,
        }
        # Join-history derived counters (best-effort — fall back to 0 if
        # the writer hasn't been initialised yet for any reason).
        try:
            total_connects_today = self.join_history.total_connects_today()
            unique_names_seen = self.join_history.unique_names_count()
        except Exception:
            total_connects_today = 0
            unique_names_seen = 0
        # The unified admin portal renders top-level keys from /api/state's
        # `game` dict as state-cards. Surface a few MMM-specific values plus
        # the join-history counters there so they appear in the dashboard.
        game = {
            "active": self.game_active,
            "track_id": self.sim.track_id if self.sim else 0,
            "current_lap": (max((p.lap for p in self.sim.players.values()),
                                default=0)
                            if self.sim else 0),
            "lap_count": (self.sim.lap_count if self.sim
                          else self.tuning["lap_count"]),
            "racers_finished": (sum(1 for p in self.sim.players.values()
                                    if p.finished)
                                if self.sim else 0),
            "human_count": sum(1 for c in self.clients.values()
                               if c.authenticated),
            "bot_count": len(self.bots),
            "total_connects_today": total_connects_today,
            "unique_names_seen": unique_names_seen,
        }
        return {
            "uptime": round(now - self._start_time, 1),
            "players": players,
            "bots": bots,
            "match": match,
            "game": game,
            "tuning": dict(self.tuning),
            "total_connects_today": total_connects_today,
            "unique_names_seen": unique_names_seen,
        }


# ==========================================================================
# Admin HTTP handler
# ==========================================================================


ADMIN_HTML_STUB = b"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>MicroMotorMayhem Admin</title></head><body style="font-family:monospace;background:#1a1a2e;color:#e0e0e0;padding:24px">
<h1>MicroMotorMayhem Admin</h1>
<p>This service exposes the MMM admin JSON API.</p>
<p>Visit <a style="color:#f5a623" href="/admin/">the unified Saturn admin portal</a> for the dashboard.</p>
</body></html>"""


def _make_mmm_admin_handler(server_ref):
    class MMMAdminHandler(BaseHTTPRequestHandler):
        srv = server_ref

        def log_message(self, fmt, *args):
            log.debug("Admin HTTP: " + fmt, *args)

        def _check_auth(self):
            if self.headers.get("X-Admin-Auth") == "nginx-verified":
                return True
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Basic "):
                self._send_auth_required()
                return False
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, pwd = decoded.split(":", 1)
            except Exception:
                self._send_auth_required()
                return False
            srv = self.srv
            if user != srv._admin_user or pwd != srv._admin_password:
                self._send_auth_required()
                return False
            return True

        def _send_auth_required(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate",
                             'Basic realm="MMM Admin"')
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "12")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            self.close_connection = True

        def _send_json(self, data, code=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            if not self._check_auth():
                return
            path = urlparse(self.path).path
            if path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(ADMIN_HTML_STUB)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(ADMIN_HTML_STUB)
                self.close_connection = True
            elif path == "/api/state":
                self._send_json(self.srv._build_admin_state())
            elif path == "/api/leaderboard":
                self._send_json({
                    "entries": self.srv.leaderboard.top_entries(50),
                    "raw": self.srv.leaderboard.data,
                })
            elif path == "/api/join_history":
                # ?limit=N (default 100, max 1000) — newest events first.
                qs = parse_qs(urlparse(self.path).query)
                limit_raw = qs.get("limit", ["100"])[0]
                try:
                    n = int(limit_raw)
                except (TypeError, ValueError):
                    n = 100
                if n < 1:
                    n = 1
                if n > 1000:
                    n = 1000
                events = self.srv.join_history.recent(n)
                self._send_json({
                    "events": events,
                    "count": len(events),
                    "total_connects_today":
                        self.srv.join_history.total_connects_today(),
                    "unique_names_seen":
                        self.srv.join_history.unique_names_count(),
                })
            elif path == "/api/client_logs":
                # ?lines=N (default 100, max 1000) — last N lines of
                # mmm_client.log for remote troubleshooting.
                qs = parse_qs(urlparse(self.path).query)
                lines_raw = qs.get("lines", ["100"])[0]
                try:
                    n = int(lines_raw)
                except (TypeError, ValueError):
                    n = 100
                if n < 1:
                    n = 100
                if n > 1000:
                    n = 1000
                tail = self.srv.client_log.tail(n)
                self._send_json({"lines": tail, "count": len(tail)})
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._check_auth():
                return
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            srv = self.srv
            if path == "/api/add_bot":
                srv._admin_command_queue.put({"cmd": "add_bot"})
                self._send_json({"message": "Add bot queued"})
            elif path == "/api/remove_bot":
                srv._admin_command_queue.put({"cmd": "remove_bot"})
                self._send_json({"message": "Remove bot queued"})
            elif path == "/api/force_end_race":
                srv._admin_command_queue.put({"cmd": "force_end_race"})
                self._send_json({"message": "Force end queued"})
            elif path == "/api/upload_waypoints":
                # Operator-uploaded real waypoint table for a given track.
                # Body: {"waypoints": [{"section":S,"x":X,"y":Y,"z":Z}, ...],
                #        "total": N, "checkpoints": [...]}
                # Stored into the module-level `track_waypoints` dict; BotPlayer
                # tick_ai/update_waypoint pick it up automatically. See the
                # alpha 0.1 limitation comment block at BotPlayer in mserver.py.
                tid_raw = qs.get("track_id", [data.get("track_id", "")])[0]
                try:
                    tid = int(tid_raw)
                except (TypeError, ValueError):
                    self._send_json({"error": "bad track_id"}, 400)
                    return
                if tid < TRACK_MIN or tid > TRACK_MAX:
                    self._send_json(
                        {"error": "track_id out of range [%d,%d]" %
                         (TRACK_MIN, TRACK_MAX)}, 400)
                    return
                wps_raw = data.get("waypoints")
                if not isinstance(wps_raw, list) or not wps_raw:
                    self._send_json(
                        {"error": "waypoints must be a non-empty list"}, 400)
                    return
                pts = []
                try:
                    for entry in wps_raw:
                        # Section offset is informational; we collapse into
                        # absolute (x,y,z) here just like the C code does at
                        # main.c:5186-5188 (waypoint + map_section offset).
                        x = int(entry.get("x", 0))
                        y = int(entry.get("y", 0))
                        z = int(entry.get("z", 0))
                        pts.append((x, y, z))
                except (TypeError, ValueError, AttributeError):
                    self._send_json(
                        {"error": "waypoint entries must have int x/y/z"}, 400)
                    return
                track_waypoints[tid] = pts
                # Drop any cached procedural fallback for this track so the
                # next _get_track_waypoints call returns the real data.
                _TRACK_LOOP_CACHE.pop(tid, None)
                log.info("Admin uploaded %d waypoints for track %d",
                         len(pts), tid)
                self._send_json({"message": "Waypoints stored",
                                 "track_id": tid, "count": len(pts)})
            elif path == "/api/kick":
                pid_raw = qs.get("player_id", [data.get("player_id", "")])[0]
                try:
                    pid = int(pid_raw)
                except (TypeError, ValueError):
                    self._send_json({"error": "bad player_id"}, 400)
                    return
                srv._admin_command_queue.put({"cmd": "kick", "player_id": pid})
                self._send_json({"message": "Kick queued"})
            elif path == "/api/tune":
                key = qs.get("key", [data.get("key", "")])[0]
                val = qs.get("value", [data.get("value", "")])[0]
                if key not in srv.tuning:
                    self._send_json({"error": "unknown key: %s" % key}, 400)
                    return
                # Range-validate the int knobs to keep the game loop honest.
                if key == "bot_count":
                    try:
                        v = int(val)
                    except (TypeError, ValueError):
                        self._send_json({"error": "bot_count must be int"}, 400)
                        return
                    if v < 0 or v > MAX_PLAYERS - 1:
                        self._send_json(
                            {"error": "bot_count out of range [0,%d]" %
                             (MAX_PLAYERS - 1)}, 400)
                        return
                elif key == "lap_count":
                    try:
                        v = int(val)
                    except (TypeError, ValueError):
                        self._send_json({"error": "lap_count must be int"}, 400)
                        return
                    if v < 1 or v > 9:
                        self._send_json(
                            {"error": "lap_count out of range [1,9]"}, 400)
                        return
                elif key == "powerup_respawn_secs":
                    try:
                        v = int(val)
                    except (TypeError, ValueError):
                        self._send_json(
                            {"error": "powerup_respawn_secs must be int"}, 400)
                        return
                    if v < 0 or v > 120:
                        self._send_json(
                            {"error": "respawn out of range [0,120]"}, 400)
                        return
                elif key == "forced_track_id":
                    try:
                        v = int(val)
                    except (TypeError, ValueError):
                        self._send_json(
                            {"error": "forced_track_id must be int"}, 400)
                        return
                    if v < TRACK_MIN or v > TRACK_MAX:
                        self._send_json(
                            {"error": "forced_track_id out of range [%d,%d]" %
                             (TRACK_MIN, TRACK_MAX)}, 400)
                        return
                # bot_count is a hint for next start; actual bot list reflects
                # adds/removes via /api/add_bot etc.
                srv._admin_command_queue.put(
                    {"cmd": "tune", "key": key, "value": val})
                self._send_json({"message": "Tune queued",
                                 "key": key, "value": val})
            else:
                self.send_error(404)

    return MMMAdminHandler


# ==========================================================================
# CLI
# ==========================================================================


def main():
    parser = argparse.ArgumentParser(
        description="MicroMotorMayhem NetLink Game Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=4826, help="Bind port")
    parser.add_argument("--bots", type=int, default=0,
                        help="Number of server-side bot players (0-3)")
    parser.add_argument("--admin-port", type=int, default=0,
                        help="Admin HTTP port (0=disabled)")
    parser.add_argument("--admin-user", default="admin",
                        help="Admin username (for direct-port access)")
    parser.add_argument("--admin-password", default="mmm2026",
                        help="Admin password (for direct-port access)")
    parser.add_argument("--verbose", action="store_true",
                        help="Debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Clamp bots to MAX_PLAYERS-1 so a real player can always join.
    bots = max(0, min(args.bots, MAX_PLAYERS - 1))
    if bots != args.bots:
        log.warning("Clamped --bots from %d to %d", args.bots, bots)

    server = MMMServer(host=args.host, port=args.port,
                       num_bots=bots,
                       admin_port=args.admin_port,
                       admin_user=args.admin_user,
                       admin_password=args.admin_password)
    if bots > 0:
        log.info("Starting with %d bot(s): %s", bots,
                 ", ".join(b.name for b in server.bots))
    try:
        server.start()
    except KeyboardInterrupt:
        log.info("Server shutting down")


if __name__ == "__main__":
    main()
