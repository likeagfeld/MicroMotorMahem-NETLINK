#!/usr/bin/env python3
"""
Smoke tests for MMM server protocol code.

Verifies:
  1. SNCP frame encoding / decoding (the most failure-prone area).
  2. Powerup-spawn type-roll determinism (given the same seed, the same
     POWERUP_SPAWN type sequence emerges across server restarts).
  3. Various message builder lengths / wire layouts match mmm_protocol.h.

Run before deploying mserver.py:
    python3 test_quantize.py
exit: 0 = all pass, 1 = any failure
"""

import os
import random
import struct
import sys

# Import builders from the server module so this is a regression check, not
# a parallel reimplementation.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mserver  # noqa: E402


failures = 0
def check(name, got, expected):
    global failures
    if got != expected:
        print(f"  FAIL {name}: got {got!r}, expected {expected!r}")
        failures += 1


# === Test 1: SNCP framing round-trip ==========================================
print("Test 1: SNCP frame round-trip")
def parse_sncp(stream: bytes):
    """Mirror of MMMServer._handle_client_data parser. Returns list of payloads."""
    out = []
    buf = stream
    while len(buf) >= 2:
        plen = (buf[0] << 8) | buf[1]
        if plen == 0:
            raise ValueError("zero-length frame")
        if len(buf) < 2 + plen:
            break  # incomplete
        out.append(buf[2:2 + plen])
        buf = buf[2 + plen:]
    return out, buf

# Three back-to-back frames in one stream:
f1 = mserver.encode_frame(b"\xA0\x02hello")
f2 = mserver.encode_frame(b"\xA1")
f3 = mserver.encode_frame(b"\xAA\x00\x07\x00\x00\x00\x00\x00\x00")
stream = f1 + f2 + f3
payloads, leftover = parse_sncp(stream)
check("frame_count", len(payloads), 3)
check("leftover", leftover, b"")
check("frame1", payloads[0], b"\xA0\x02hello")
check("frame2", payloads[1], b"\xA1")
check("frame3.len", len(payloads[2]), 9)

# Partial frame: feed first frame plus 1 header byte of next.
partial = f1 + b"\x00"
payloads, leftover = parse_sncp(partial)
check("partial.count", len(payloads), 1)
check("partial.leftover", leftover, b"\x00")


# === Test 2: build_game_start wire layout =====================================
print("Test 2: build_game_start layout matches mmm_protocol.h")
# [type:1][seed:4 BE][my_player_id:1][opp_count:1][track_id:1][lap_count:1] = 9
msg = mserver.build_game_start(seed=0xDEADBEEF, my_player_id=2,
                               opponent_count=3, track_id=7, lap_count=3)
# Frame = [LEN_HI][LEN_LO][PAYLOAD]; payload should be 9 bytes.
check("game_start.frame_len", len(msg), 2 + 9)
plen = (msg[0] << 8) | msg[1]
check("game_start.payload_len", plen, 9)
check("game_start.opcode", msg[2], mserver.MNET_MSG_GAME_START)
seed = struct.unpack("!I", msg[3:7])[0]
check("game_start.seed", seed, 0xDEADBEEF)
check("game_start.my_pid", msg[7], 2)
check("game_start.opp_count", msg[8], 3)
check("game_start.track_id", msg[9], 7)
check("game_start.lap_count", msg[10], 3)


# === Test 3: build_player_sync layout =========================================
print("Test 3: build_player_sync layout (PLAYER_SYNC = 14-byte payload)")
# [type:1][pid:1][x:2][y:2][z:2][ry:2][speed:2][lap:1][cp:1][cur_wp:1][dist_wp:2] = 17
msg = mserver.build_player_sync(player_id=1, x=100, y=0, z=-200,
                                ry=45, speed=1024,
                                lap=2, checkpoint=3, cur_wp=5,
                                dist_wp=600)
plen = (msg[0] << 8) | msg[1]
check("sync.payload_len", plen, 17)
check("sync.opcode", msg[2], mserver.MNET_MSG_PLAYER_SYNC)
check("sync.pid", msg[3], 1)
x, y, z, ry, speed = struct.unpack("!hhhhh", msg[4:14])
check("sync.x", x, 100)
check("sync.z", z, -200)  # negative round-trips correctly
check("sync.speed", speed, 1024)
check("sync.lap", msg[14], 2)
check("sync.cp", msg[15], 3)
check("sync.cur_wp", msg[16], 5)
dist = (msg[17] << 8) | msg[18]
check("sync.dist_wp", dist, 600)


# === Test 4: build_player_sync int16 saturation ==============================
print("Test 4: PLAYER_SYNC saturates int16 cleanly (no struct overflow)")
# Server should clamp out-of-range values rather than raise.
msg = mserver.build_player_sync(player_id=0, x=99999, y=0, z=-99999,
                                ry=999, speed=-50000,
                                lap=0, checkpoint=0, cur_wp=0, dist_wp=0)
x, y, z, _ry, speed = struct.unpack("!hhhhh", msg[4:14])
check("sat.x_clamped", x, 32767)
check("sat.z_clamped", z, -32768)
check("sat.speed_clamped", speed, -32768)


# === Test 5: build_lobby_state ================================================
print("Test 5: build_lobby_state encodes [count][{id,name_lp,car,ready,is_local}]")
players = [
    {"id": 1, "name": "GARY",   "car": 0, "ready": True,  "is_local": False},
    {"id": 1, "name": "GARY-2", "car": 1, "ready": True,  "is_local": True},
    {"id": 200, "name": "PIXIE", "car": 2, "ready": True, "is_local": False},
]
msg = mserver.build_lobby_state(players)
plen = (msg[0] << 8) | msg[1]
check("lobby.opcode", msg[2], mserver.MNET_MSG_LOBBY_STATE)
check("lobby.count", msg[3], 3)
# Walk the entries:
off = 4
for p in players:
    check("lobby.id", msg[off], p["id"] & 0xFF); off += 1
    nlen = msg[off]; off += 1
    name = msg[off:off + nlen].decode("utf-8"); off += nlen
    check("lobby.name", name, p["name"])
    check("lobby.car", msg[off], p["car"]); off += 1
    check("lobby.ready", msg[off], 1 if p["ready"] else 0); off += 1
    check("lobby.is_local", msg[off], 1 if p["is_local"] else 0); off += 1
# Frame should account for all bytes
check("lobby.payload_consumed", off - 2, plen)


# === Test 6: build_powerup_spawn ==============================================
print("Test 6: build_powerup_spawn layout")
msg = mserver.build_powerup_spawn(slot=3, ptype=5, x=10, y=20, z=-30)
plen = (msg[0] << 8) | msg[1]
# [type:1][slot:1][ptype:1][x:2][y:2][z:2] = 9
check("ps.payload_len", plen, 9)
check("ps.opcode", msg[2], mserver.MNET_MSG_POWERUP_SPAWN)
check("ps.slot", msg[3], 3)
check("ps.type", msg[4], 5)
x, y, z = struct.unpack("!hhh", msg[5:11])
check("ps.x", x, 10)
check("ps.z", z, -30)


# === Test 7: build_race_finish standings encoding =============================
print("Test 7: build_race_finish encodes winner + standings")
standings = [(0, 1, 95), (1, 2, 100), (2, 3, 110)]
msg = mserver.build_race_finish(winner_id=0, standings=standings)
plen = (msg[0] << 8) | msg[1]
# [type][winner][count] + 3 entries x [pid:1][pos:1][total:2] = 3 + 12 = 15
check("rf.payload_len", plen, 3 + 12)
check("rf.opcode", msg[2], mserver.MNET_MSG_RACE_FINISH)
check("rf.winner", msg[3], 0)
check("rf.count", msg[4], 3)
off = 5
for pid, pos, total in standings:
    check("rf.pid", msg[off], pid); off += 1
    check("rf.pos", msg[off], pos); off += 1
    t = (msg[off] << 8) | msg[off + 1]; off += 2
    check("rf.total", t, total)


# === Test 8: powerup-spawn type roll is seed-deterministic ====================
print("Test 8: identical seed produces identical powerup type sequence")
SEED = 0xCAFEBABE
sim_a = mserver.GameSimulation(track_id=3, seed=SEED, lap_count=3)
sim_b = mserver.GameSimulation(track_id=3, seed=SEED, lap_count=3)
events_a = sim_a.initial_powerup_spawn()
events_b = sim_b.initial_powerup_spawn()
check("roll.same_count", len(events_a), len(events_b))
for i, (a, b) in enumerate(zip(events_a, events_b)):
    # event = ('powerup_spawn', slot, ptype, x, y, z)
    check(f"roll.slot[{i}]", a[1], b[1])
    check(f"roll.type[{i}]", a[2], b[2])
# Different seed should diverge.
sim_c = mserver.GameSimulation(track_id=3, seed=SEED + 1, lap_count=3)
events_c = sim_c.initial_powerup_spawn()
diverged = any(events_a[i][2] != events_c[i][2] for i in range(len(events_a)))
check("roll.diverges_with_diff_seed", diverged, True)
# All rolled types fall in [1,7].
for evt in events_a:
    pt = evt[2]
    if pt < mserver.POWERUP_TYPE_MIN or pt > mserver.POWERUP_TYPE_MAX:
        print(f"  FAIL roll.type_in_range: {pt} not in [1,7]")
        failures += 1


# === Test 9: respawn re-roll uses same RNG stream =============================
print("Test 9: powerup respawn continues the seeded RNG stream")
SEED = 0x12345678
sim = mserver.GameSimulation(track_id=1, seed=SEED, lap_count=3)
# handle_powerup_pickup validates pid is in self.players, so register one first.
sim.players[0] = mserver.Player(0, "DUMMY")
initial = sim.initial_powerup_spawn()
# Take slot 2 immediately; the re-spawn should pick the next RNG value.
evt, ok = sim.handle_powerup_pickup(pid=0, slot=2)
check("pickup.accepted", ok, True)
# Force respawn by backdating respawn_at.
import time
sim.powerup_state[2]["respawn_at"] = time.time() - 1.0
sim2 = mserver.GameSimulation(track_id=1, seed=SEED, lap_count=3)
_ = sim2.initial_powerup_spawn()  # consume first 8 rolls
# The 9th roll from sim2 should equal the respawn type from sim.
expected_next_type = sim2._powerup_rng.randint(
    mserver.POWERUP_TYPE_MIN, mserver.POWERUP_TYPE_MAX)
respawn_evts = sim.tick_powerup_respawns()
check("respawn.fired_count", len(respawn_evts), 1)
if respawn_evts:
    check("respawn.type_matches_stream", respawn_evts[0][2], expected_next_type)


# === Test 10: input bitmask constants match protocol header ==================
print("Test 10: input bitmask constants")
check("INPUT_GAS",    mserver.MNET_INPUT_GAS,    1 << 0)
check("INPUT_BRAKE",  mserver.MNET_INPUT_BRAKE,  1 << 1)
check("INPUT_LEFT",   mserver.MNET_INPUT_LEFT,   1 << 2)
check("INPUT_RIGHT",  mserver.MNET_INPUT_RIGHT,  1 << 3)
check("INPUT_ACTION", mserver.MNET_INPUT_ACTION, 1 << 4)
check("INPUT_START",  mserver.MNET_INPUT_START,  1 << 5)
check("INPUT_HORN",   mserver.MNET_INPUT_HORN,   1 << 6)


print()
if failures == 0:
    print("All tests passed.")
    sys.exit(0)
else:
    print(f"{failures} failure(s).")
    sys.exit(1)
