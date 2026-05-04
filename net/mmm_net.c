/**
 * mmm_net.c - MicroMotorMayhem Networking State Machine
 *
 * Adapted from disasteroids_net.c. Sync model: passthrough — client-auth
 * movement (we send INPUT_STATE every frame on edge, PLAYER_STATE every
 * 4 frames as keepalive); server-auth lap/checkpoint/powerup-type/finish.
 */

#include <string.h>
#include <stdio.h>
#include "mmm_net.h"

/* Local scratch buffer for formatted client-log lines. ≤80 + slack. */
#define MNET_CLIENT_LOG_SCRATCH 96

/*============================================================================
 * Global state
 *============================================================================*/

mnet_state_data_t g_mnet;

/*============================================================================
 * Init / config
 *============================================================================*/

void mnet_init(void)
{
    int i;

    memset(&g_mnet, 0, sizeof(g_mnet));
    g_mnet.state = MNET_STATE_OFFLINE;
    g_mnet.modem_available = false;
    g_mnet.transport = (void*)0;
    g_mnet.status_msg = "Offline";
    g_mnet.my_player_id   = MNET_INVALID_PLAYER_ID;
    g_mnet.my_player_id_2 = MNET_INVALID_PLAYER_ID;
    for (i = 0; i < MNET_POWERUP_SLOTS; i++) {
        g_mnet.powerup_types[i] = 0xFF;
        g_mnet.powerup_active[i] = false;
    }
    mnet_rx_init(&g_mnet.rx, g_mnet.rx_buf, sizeof(g_mnet.rx_buf));

    /* Rate limiter: bumped from 4 to 32 because the START_RACE phase logs
     * fire 8 messages back-to-back during CD I/O — the smaller bucket was
     * silently dropping diagnostic frames mid-debug. 32 tokens gives
     * headroom for any rapid-fire burst while the +1/15-frames refill
     * caps the long-run rate at ~4 msg/sec (well within 14400 baud). */
    g_mnet.client_log_tokens = 64;  /* bumped for diagnostic build — race-start burst has ~26 messages */
    g_mnet.client_log_refill = 0;
    g_mnet.client_log_dropped = 0;
}

void mnet_set_modem_available(bool available) { g_mnet.modem_available = available; }
void mnet_set_transport(const net_transport_t* transport) { g_mnet.transport = transport; }

void mnet_set_username(const char* name)
{
    int i;
    for (i = 0; i < MNET_MAX_NAME && name[i]; i++) g_mnet.my_name[i] = name[i];
    g_mnet.my_name[i] = '\0';
}

void mnet_set_username_2(const char* name)
{
    int i;
    for (i = 0; i < MNET_MAX_NAME && name[i]; i++) g_mnet.my_name_2[i] = name[i];
    g_mnet.my_name_2[i] = '\0';
    g_mnet.has_local_p2 = (i > 0);
}

mnet_state_t mnet_get_state(void) { return g_mnet.state; }

/*============================================================================
 * Logging
 *============================================================================*/

void mnet_log(const char* msg)
{
    int i, dst;
    if (g_mnet.log_count < 4) {
        dst = g_mnet.log_count;
    } else {
        for (i = 0; i < 3; i++)
            memcpy(g_mnet.log_lines[i], g_mnet.log_lines[i + 1], 40);
        dst = 3;
    }
    for (i = 0; i < 39 && msg[i]; i++) g_mnet.log_lines[dst][i] = msg[i];
    g_mnet.log_lines[dst][i] = '\0';
    if (g_mnet.log_count < 4) g_mnet.log_count++;
}

void mnet_clear_log(void)
{
    memset(g_mnet.log_lines, 0, sizeof(g_mnet.log_lines));
    g_mnet.log_count = 0;
}

/*============================================================================
 * Client → Server log (remote troubleshooting)
 *============================================================================*/

void mnet_client_log(uint8_t level, const char* text)
{
    int len;

    if (!text) return;

    /* Always mirror locally so on-screen log overlays still see the line
     * even if rate-limit drops the wire send. */
    mnet_log(text);

    /* Pre-connection: no server to receive. Silent skip. */
    if (g_mnet.state < MNET_STATE_AUTHENTICATING) return;
    if (g_mnet.state == MNET_STATE_DISCONNECTED) return;
    if (!g_mnet.transport) return;

    /* Token bucket: drop silently if exhausted. Saturn → 14400 baud, the
     * INPUT_STATE / PLAYER_STATE traffic is the priority. */
    if (g_mnet.client_log_tokens <= 0) {
        if (g_mnet.client_log_dropped < 0xFFFF) g_mnet.client_log_dropped++;
        return;
    }
    g_mnet.client_log_tokens--;

    len = mnet_encode_client_log(g_mnet.tx_buf, level, text);
    if (len > 0 && len <= MNET_TX_FRAME_SIZE) {
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
    }
}

/*============================================================================
 * Transitions
 *============================================================================*/

void mnet_enter_offline(void)
{
    g_mnet.state = MNET_STATE_OFFLINE;
    g_mnet.status_msg = "Offline";
}

void mnet_on_connected(void)
{
    int len;

    mnet_rx_init(&g_mnet.rx, g_mnet.rx_buf, sizeof(g_mnet.rx_buf));

    g_mnet.state = MNET_STATE_AUTHENTICATING;
    g_mnet.status_msg = "Authenticating...";
    g_mnet.auth_timer = 0;
    g_mnet.auth_retries = 0;
    g_mnet.heartbeat_counter = 0;

    if (g_mnet.has_uuid) {
        len = mnet_encode_connect_uuid(g_mnet.tx_buf, g_mnet.my_uuid);
    } else {
        len = mnet_encode_connect(g_mnet.tx_buf);
    }
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);

    mnet_log("SENT CONNECT");
    /* Lifecycle log: server side records the moment the Saturn flips into
     * AUTH so we can correlate against bridge accept-time. */
    MNET_LOG_INFO("Connected; sending CONNECT");
}

/*============================================================================
 * Big-endian helpers
 *============================================================================*/

static inline int16_t read_i16(const uint8_t* p)
{
    return (int16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

static inline uint16_t read_u16(const uint8_t* p)
{
    return ((uint16_t)p[0] << 8) | (uint16_t)p[1];
}

/*============================================================================
 * Message processors
 *============================================================================*/

static void process_welcome(const uint8_t* payload, int len)
{
    int off, i;
    if (len < 2) return;

    g_mnet.my_player_id = payload[1];
    off = 2;

    if (off + SNCP_UUID_LEN <= len) {
        for (i = 0; i < SNCP_UUID_LEN; i++)
            g_mnet.my_uuid[i] = (char)payload[off + i];
        g_mnet.my_uuid[SNCP_UUID_LEN] = '\0';
        g_mnet.has_uuid = true;
        off += SNCP_UUID_LEN;
    }

    if (off < len) {
        mnet_read_string(&payload[off], len - off,
                         g_mnet.my_name, MNET_MAX_NAME + 1);
    }

    g_mnet.state = MNET_STATE_LOBBY;
    g_mnet.status_msg = "In Lobby";
    g_mnet.connected_event = true;
    mnet_log("WELCOME!");

    {
        char buf[MNET_CLIENT_LOG_SCRATCH];
        sprintf(buf, "Auth OK pid=%d state->LOBBY", (int)g_mnet.my_player_id);
        MNET_LOG_INFO(buf);
    }

    /* If we already had a 2nd local player queued, register them now. */
    if (g_mnet.has_local_p2 && g_mnet.my_name_2[0] != '\0') {
        int slen = mnet_encode_add_local_player(g_mnet.tx_buf, g_mnet.my_name_2);
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, slen);
        mnet_log("REGISTERING P2...");
    }
}

static void process_username_required(void)
{
    int len;
    g_mnet.state = MNET_STATE_USERNAME;
    g_mnet.status_msg = "Enter username";
    if (g_mnet.my_name[0] != '\0') {
        len = mnet_encode_set_username(g_mnet.tx_buf, g_mnet.my_name);
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
        g_mnet.state = MNET_STATE_AUTHENTICATING;
        g_mnet.status_msg = "Authenticating...";
    }
}

static void process_lobby_state(const uint8_t* payload, int len)
{
    int off, i, consumed;
    if (len < 2) return;

    g_mnet.lobby_count = payload[1];
    if (g_mnet.lobby_count > MNET_MAX_PLAYERS)
        g_mnet.lobby_count = MNET_MAX_PLAYERS;

    off = 2;
    for (i = 0; i < g_mnet.lobby_count && off < len; i++) {
        if (off >= len) break;
        g_mnet.lobby_players[i].id = payload[off++];

        consumed = mnet_read_string(&payload[off], len - off,
                                     g_mnet.lobby_players[i].name,
                                     MNET_MAX_NAME + 1);
        if (consumed < 0) break;
        off += consumed;

        if (off < len) g_mnet.lobby_players[i].car_id = payload[off++];
        if (off < len) g_mnet.lobby_players[i].ready = (payload[off++] != 0);
        if (off < len) g_mnet.lobby_players[i].is_local = (payload[off++] != 0);

        g_mnet.lobby_players[i].active = true;
    }

    for (; i < MNET_MAX_PLAYERS; i++) g_mnet.lobby_players[i].active = false;
}

static void process_game_start(const uint8_t* payload, int len)
{
    int i;
    if (len < 8) return;

    /* [type:1][seed:4 BE][my_pid:1][opp_count:1][track:1][laps:1] */
    g_mnet.game_seed = ((uint32_t)payload[1] << 24)
                     | ((uint32_t)payload[2] << 16)
                     | ((uint32_t)payload[3] << 8)
                     | ((uint32_t)payload[4]);
    g_mnet.my_player_id = payload[5];
    g_mnet.opponent_count = payload[6];
    g_mnet.track_id = payload[7];
    g_mnet.lap_count = (len > 8) ? payload[8] : 3;

    if (g_mnet.my_player_id >= MNET_MAX_PLAYERS) {
        char buf[MNET_CLIENT_LOG_SCRATCH];
        mnet_log("BAD PLAYER ID!");
        sprintf(buf, "Bad pid in GAME_START: %d", (int)g_mnet.my_player_id);
        MNET_LOG_ERROR(buf);
        g_mnet.state = MNET_STATE_DISCONNECTED;
        g_mnet.status_msg = "Server error";
        return;
    }

    g_mnet.has_last_results = false;
    g_mnet.race_finished = false;
    g_mnet.finish_count = 0;
    g_mnet.game_start_pending = true;

    g_mnet.state = MNET_STATE_PLAYING;
    g_mnet.status_msg = "Playing";
    g_mnet.local_frame = 0;
    g_mnet.last_sent_input = 0;
    g_mnet.send_cooldown = 15;
    g_mnet.player_state_cooldown = 4;
    g_mnet.last_sent_input_p2 = 0;
    g_mnet.send_cooldown_p2 = 15;
    g_mnet.player_state_cooldown_p2 = 4;

    memset(g_mnet.remote_inputs, 0, sizeof(g_mnet.remote_inputs));
    memset(g_mnet.remote_input_head, 0, sizeof(g_mnet.remote_input_head));
    memset(g_mnet.remote_states, 0, sizeof(g_mnet.remote_states));

    /* Reset sync diagnostic counters per race. */
    memset(g_mnet.diag_rx_input_relay, 0, sizeof(g_mnet.diag_rx_input_relay));
    memset(g_mnet.diag_rx_player_sync, 0, sizeof(g_mnet.diag_rx_player_sync));
    memset(g_mnet.diag_last_sync_frame, 0, sizeof(g_mnet.diag_last_sync_frame));
    memset(g_mnet.diag_last_sync_x, 0, sizeof(g_mnet.diag_last_sync_x));
    memset(g_mnet.diag_last_sync_z, 0, sizeof(g_mnet.diag_last_sync_z));

    /* Reset powerup roll cache. Server will broadcast spawns. */
    for (i = 0; i < MNET_POWERUP_SLOTS; i++) {
        g_mnet.powerup_types[i] = 0xFF;
        g_mnet.powerup_active[i] = false;
    }

    /* Wipe lobby — server resends roster via PLAYER_JOIN with race IDs. */
    memset(g_mnet.lobby_players, 0, sizeof(g_mnet.lobby_players));
    g_mnet.lobby_count = 0;
    memset(g_mnet.game_roster, 0, sizeof(g_mnet.game_roster));
    g_mnet.game_roster_count = 0;

    mnet_log("RACE STARTING!");
    {
        char buf[MNET_CLIENT_LOG_SCRATCH];
        sprintf(buf, "GAME_START seed=%lu track=%d laps=%d state->PLAYING",
                (unsigned long)g_mnet.game_seed,
                (int)g_mnet.track_id,
                (int)g_mnet.lap_count);
        MNET_LOG_INFO(buf);
    }
}

static void process_input_relay(const uint8_t* payload, int len)
{
    uint8_t pid;
    uint16_t frame_num;
    uint8_t input_bits;
    int idx;

    if (len < 5) return;
    /* [type:1][pid:1][frame:2][input:1] = 5 */
    pid = payload[1];
    frame_num = ((uint16_t)payload[2] << 8) | (uint16_t)payload[3];
    input_bits = payload[4];

    /* Skip echoes of our own input. */
    if (pid == g_mnet.my_player_id) return;
    if (g_mnet.my_player_id_2 != MNET_INVALID_PLAYER_ID && pid == g_mnet.my_player_id_2) return;
    if (pid >= MNET_MAX_PLAYERS) return;

    idx = g_mnet.remote_input_head[pid] % MNET_INPUT_BUFFER_PER_PLAYER;
    g_mnet.remote_inputs[pid][idx].frame_num = frame_num;
    g_mnet.remote_inputs[pid][idx].input_bits = input_bits;
    g_mnet.remote_inputs[pid][idx].player_id = pid;
    g_mnet.remote_inputs[pid][idx].valid = true;
    g_mnet.remote_input_head[pid]++;
    g_mnet.diag_rx_input_relay[pid]++;
}

static void process_player_sync(const uint8_t* payload, int len)
{
    uint8_t pid;
    mnet_remote_state_t* rs;

    /* [type:1][pid:1][x:2][y:2][z:2][ry:2][speed:2][lap:1][cp:1][cur_wp:1][dist_wp:2] = 17 */
    if (len < 17) return;

    pid = payload[1];
    if (pid >= MNET_MAX_PLAYERS) return;

    /* Don't apply server snapshots to our own slot — we're authoritative
     * over local movement. */
    if (pid == g_mnet.my_player_id) return;
    if (g_mnet.my_player_id_2 != MNET_INVALID_PLAYER_ID && pid == g_mnet.my_player_id_2) return;

    rs = &g_mnet.remote_states[pid];
    rs->valid = true;
    rs->x = read_i16(&payload[2]);
    rs->y = read_i16(&payload[4]);
    rs->z = read_i16(&payload[6]);
    rs->ry = read_i16(&payload[8]);
    rs->speed = read_i16(&payload[10]);
    rs->lap = payload[12];
    rs->checkpoint = payload[13];
    rs->cur_wp = payload[14];
    rs->dist_wp = read_u16(&payload[15]);
    rs->last_sync_frame = (uint16_t)g_mnet.frame_count;
    g_mnet.diag_rx_player_sync[pid]++;
    g_mnet.diag_last_sync_frame[pid] = (uint16_t)g_mnet.local_frame;
    g_mnet.diag_last_sync_x[pid] = rs->x;
    g_mnet.diag_last_sync_z[pid] = rs->z;
}

static void process_powerup_spawn(const uint8_t* payload, int len)
{
    uint8_t slot;
    /* [type:1][slot:1][ptype:1][x:2][y:2][z:2] = 9 */
    if (len < 9) return;

    slot = payload[1];
    if (slot >= MNET_POWERUP_SLOTS) return;

    g_mnet.powerup_types[slot]  = payload[2];
    g_mnet.powerup_x[slot]      = read_i16(&payload[3]);
    g_mnet.powerup_y[slot]      = read_i16(&payload[5]);
    g_mnet.powerup_z[slot]      = read_i16(&payload[7]);
    g_mnet.powerup_active[slot] = true;
}

static void process_powerup_destroy(const uint8_t* payload, int len)
{
    uint8_t slot;
    /* [type:1][slot:1][taker:1] = 3 */
    if (len < 3) return;
    slot = payload[1];
    if (slot >= MNET_POWERUP_SLOTS) return;
    g_mnet.powerup_active[slot] = false;
    /* Keep type for re-roll on next spawn until server re-announces. */
}

static void process_powerup_effect(const uint8_t* payload, int len)
{
    /* [type:1][pid:1][effect:1] - reserved for future use; logged only. */
    (void)payload; (void)len;
}

static void process_lap_notify(const uint8_t* payload, int len)
{
    /* [type:1][pid:1][lap:1][position:1] - server-validated lap event */
    (void)payload; (void)len;
    /* Game-side updates lap counters via this event in the future;
     * for now the local lap counter advances on checkpoint cross. */
}

static void process_race_finish(const uint8_t* payload, int len)
{
    int off, i, count;
    /* [type:1][winner:1][count:1]{[pid:1][pos:1][total_time:2BE]}* */
    if (len < 3) return;

    g_mnet.last_winner_id = payload[1];
    count = payload[2];
    if (count > MNET_MAX_PLAYERS) count = MNET_MAX_PLAYERS;

    off = 3;
    for (i = 0; i < count && off + 4 <= len; i++) {
        g_mnet.finish[i].player_id = payload[off];
        g_mnet.finish[i].position = payload[off + 1];
        g_mnet.finish[i].total_time = read_u16(&payload[off + 2]);
        g_mnet.finish[i].active = true;
        off += 4;
    }
    for (; i < MNET_MAX_PLAYERS; i++) g_mnet.finish[i].active = false;

    g_mnet.finish_count = count;
    g_mnet.has_last_results = true;
    g_mnet.race_finished = true;
    /* Reset ready+input state so next-race A-press isn't a stale toggle.
     * Mirrors Utenyaa's unet_reset_ready_state() — fixes "ready flips off
     * on first A in the second match" bug from Utenyaa alpha 0.1. */
    g_mnet.my_ready = false;
    mnet_log("RACE FINISHED!");
    {
        char buf[MNET_CLIENT_LOG_SCRATCH];
        sprintf(buf, "RACE_FINISH winner=%d count=%d",
                (int)g_mnet.last_winner_id, (int)count);
        MNET_LOG_INFO(buf);
    }
}

static void process_game_over(const uint8_t* payload, int len)
{
    if (len >= 2) g_mnet.last_winner_id = payload[1];
    g_mnet.has_last_results = true;
    g_mnet.race_finished = true;
    g_mnet.state = MNET_STATE_LOBBY;
    g_mnet.status_msg = "In Lobby";
    /* Reset ready state on lobby re-entry — see process_race_finish comment. */
    g_mnet.my_ready = false;
    mnet_log("GAME OVER");
    MNET_LOG_INFO("GAME_OVER state->LOBBY");
}

static void process_log(const uint8_t* payload, int len)
{
    char msg[40];
    if (len < 2) return;
    mnet_read_string(&payload[1], len - 1, msg, sizeof(msg));
    mnet_log(msg);
}

static void process_leaderboard(const uint8_t* payload, int len)
{
    int off, i, nlen, copy;
    /* [type:1][count:1]{name_len:1, name:N, wins:2, best_lap:2, podiums:2, races:2, points:2}* */
    if (len < 2) return;

    g_mnet.leaderboard_count = payload[1];
    if (g_mnet.leaderboard_count > MNET_LEADERBOARD_MAX)
        g_mnet.leaderboard_count = MNET_LEADERBOARD_MAX;

    off = 2;
    for (i = 0; i < g_mnet.leaderboard_count && off < len; i++) {
        if (off >= len) break;
        nlen = payload[off++];
        copy = (nlen < MNET_MAX_NAME) ? nlen : MNET_MAX_NAME;
        if (off + nlen + 10 > len) { g_mnet.leaderboard_count = i; break; }
        memcpy(g_mnet.leaderboard[i].name, &payload[off], copy);
        g_mnet.leaderboard[i].name[copy] = '\0';
        off += nlen;
        g_mnet.leaderboard[i].wins     = read_u16(&payload[off]); off += 2;
        g_mnet.leaderboard[i].best_lap = read_u16(&payload[off]); off += 2;
        g_mnet.leaderboard[i].podiums  = read_u16(&payload[off]); off += 2;
        g_mnet.leaderboard[i].races    = read_u16(&payload[off]); off += 2;
        g_mnet.leaderboard[i].points   = read_u16(&payload[off]); off += 2;
    }
}

static void process_player_join(const uint8_t* payload, int len)
{
    uint8_t pid;
    int slot, target, rt;

    if (len < 2) return;
    pid = payload[1];

    /* Update lobby slot. */
    target = -1;
    for (slot = 0; slot < MNET_MAX_PLAYERS; slot++) {
        if (g_mnet.lobby_players[slot].active &&
            g_mnet.lobby_players[slot].id == pid) { target = slot; break; }
    }
    if (target < 0) {
        for (slot = 0; slot < MNET_MAX_PLAYERS; slot++) {
            if (!g_mnet.lobby_players[slot].active) { target = slot; break; }
        }
    }
    if (target >= 0) {
        g_mnet.lobby_players[target].id = pid;
        g_mnet.lobby_players[target].active = true;
        if (len >= 3) {
            int name_consumed = mnet_read_string(&payload[2], len - 2,
                             g_mnet.lobby_players[target].name,
                             MNET_MAX_NAME + 1);
            /* 0.7.2: server now appends car_id after the name string. Older
             * 0.7.1 server omits it; in that case name_consumed == len-2 and
             * we leave car_id at whatever it was (typically 0 from the
             * GAME_START memset, which is the pre-0.7.2 behavior). */
            if (name_consumed > 0) {
                int off = 2 + name_consumed;
                if (off < len) {
                    g_mnet.lobby_players[target].car_id = payload[off];
                }
            }
        }
        if (target >= g_mnet.lobby_count) g_mnet.lobby_count = target + 1;
    }

    /* Game roster — survives lobby reset. */
    rt = -1;
    for (slot = 0; slot < MNET_MAX_PLAYERS; slot++) {
        if (g_mnet.game_roster[slot].active && g_mnet.game_roster[slot].id == pid) {
            rt = slot; break;
        }
    }
    if (rt < 0) {
        for (slot = 0; slot < MNET_MAX_PLAYERS; slot++) {
            if (!g_mnet.game_roster[slot].active) { rt = slot; break; }
        }
    }
    if (rt >= 0) {
        g_mnet.game_roster[rt].id = pid;
        g_mnet.game_roster[rt].active = true;
        if (len >= 3) {
            mnet_read_string(&payload[2], len - 2,
                             g_mnet.game_roster[rt].name,
                             MNET_MAX_NAME + 1);
        }
        if (rt >= g_mnet.game_roster_count) g_mnet.game_roster_count = rt + 1;
    }
    if (g_mnet.state == MNET_STATE_LOBBY) mnet_log("PLAYER JOINED");
}

static void process_local_player_ack(const uint8_t* payload, int len)
{
    /* [type:1][pid:1] - 0xFF = provisional/denied */
    if (len < 2) return;
    if (payload[1] != MNET_INVALID_PLAYER_ID) {
        g_mnet.my_player_id_2 = payload[1];
        mnet_log("P2 JOINED!");
        {
            char buf[MNET_CLIENT_LOG_SCRATCH];
            sprintf(buf, "P2 added pid=%d", (int)payload[1]);
            MNET_LOG_INFO(buf);
        }
    } else {
        MNET_LOG_WARN("P2 add denied by server");
    }
}

static void process_message(const uint8_t* payload, int len)
{
    uint8_t msg_type;
    if (len < 1) return;
    msg_type = payload[0];

    switch (msg_type) {
    case SNCP_MSG_WELCOME:
    case SNCP_MSG_WELCOME_BACK:
        process_welcome(payload, len);
        break;

    case SNCP_MSG_USERNAME_REQUIRED:
        process_username_required();
        break;

    case SNCP_MSG_USERNAME_TAKEN:
    {
        if (g_mnet.username_retry < 9) {
            int nlen = 0, slen;
            g_mnet.username_retry++;
            while (g_mnet.my_name[nlen] && nlen < MNET_MAX_NAME) nlen++;
            if (nlen > 0 && g_mnet.my_name[nlen - 1] >= '1' &&
                g_mnet.my_name[nlen - 1] <= '9') nlen--;
            if (nlen < MNET_MAX_NAME) {
                g_mnet.my_name[nlen] = '0' + g_mnet.username_retry;
                g_mnet.my_name[nlen + 1] = '\0';
            }
            slen = mnet_encode_set_username(g_mnet.tx_buf, g_mnet.my_name);
            net_transport_send(g_mnet.transport, g_mnet.tx_buf, slen);
            g_mnet.state = MNET_STATE_AUTHENTICATING;
            mnet_log("NAME TAKEN, RETRYING");
        } else {
            mnet_log("ALL NAMES TAKEN!");
            g_mnet.state = MNET_STATE_DISCONNECTED;
            g_mnet.status_msg = "Name unavailable";
        }
        break;
    }

    case MNET_MSG_LOBBY_STATE:      process_lobby_state(payload, len); break;
    case MNET_MSG_GAME_START:       process_game_start(payload, len); break;
    case MNET_MSG_INPUT_RELAY:      process_input_relay(payload, len); break;
    case MNET_MSG_PLAYER_JOIN:      process_player_join(payload, len); break;
    case MNET_MSG_PLAYER_LEAVE:
    {
        mnet_log("PLAYER LEFT");
        /* If the leaving pid is ours, the server has booted us. Flag it
         * up so QA can find the boot in mmm_client.log. */
        if (len >= 2) {
            uint8_t pid = payload[1];
            if (pid == g_mnet.my_player_id) {
                MNET_LOG_WARN("Kicked by server");
            }
        }
        break;
    }
    case MNET_MSG_GAME_OVER:        process_game_over(payload, len); break;
    case MNET_MSG_LOG:              process_log(payload, len); break;
    case MNET_MSG_PLAYER_SYNC:      process_player_sync(payload, len); break;
    case MNET_MSG_POWERUP_SPAWN:    process_powerup_spawn(payload, len); break;
    case MNET_MSG_POWERUP_DESTROY:  process_powerup_destroy(payload, len); break;
    case MNET_MSG_POWERUP_EFFECT:   process_powerup_effect(payload, len); break;
    case MNET_MSG_LAP_NOTIFY:       process_lap_notify(payload, len); break;
    case MNET_MSG_RACE_FINISH:      process_race_finish(payload, len); break;
    case MNET_MSG_LOCAL_PLAYER_ACK: process_local_player_ack(payload, len); break;
    case MNET_MSG_LEADERBOARD_DATA: process_leaderboard(payload, len); break;
    default:
    {
        /* Unknown opcode — log up so the server can correlate against the
         * protocol version it just shipped. */
        char buf[MNET_CLIENT_LOG_SCRATCH];
        sprintf(buf, "Unknown opcode 0x%02X len=%d", (unsigned)msg_type, len);
        MNET_LOG_WARN(buf);
        break;
    }
    }
}

/*============================================================================
 * Tick
 *============================================================================*/

void mnet_tick(void)
{
    int processed, len;

    g_mnet.frame_count++;

    /* Client-log token bucket refill: +1 per 15 frames, cap at 32 tokens
     * → ~4 msgs/sec sustained, 32 msg burst headroom. The 32-cap matches
     * the init value in mnet_init so debug bursts during CD I/O survive. */
    g_mnet.client_log_refill++;
    if (g_mnet.client_log_refill >= 15) {
        g_mnet.client_log_refill = 0;
        if (g_mnet.client_log_tokens < 64) g_mnet.client_log_tokens++;
    }

    if (g_mnet.state == MNET_STATE_OFFLINE ||
        g_mnet.state == MNET_STATE_DISCONNECTED) return;

    if (!g_mnet.transport) return;

    processed = 0;
    while (processed < MNET_MAX_PACKETS_FRAME) {
        len = mnet_rx_poll(&g_mnet.rx, g_mnet.transport);
        if (len <= 0) break;
        process_message(g_mnet.rx_buf, len);
        processed++;
    }

    if (g_mnet.state == MNET_STATE_AUTHENTICATING) {
        g_mnet.auth_timer++;
        if (g_mnet.auth_timer >= MNET_AUTH_TIMEOUT) {
            g_mnet.auth_timer = 0;
            g_mnet.auth_retries++;
            if (g_mnet.auth_retries >= MNET_AUTH_MAX_RETRIES) {
                mnet_log("AUTH TIMEOUT");
                /* This will likely fail to send (we're disconnected from the
                 * server's POV) but the bridge may still relay it as a final
                 * breadcrumb. Best-effort. */
                MNET_LOG_ERROR("Auth failed (WELCOME timeout)");
                g_mnet.state = MNET_STATE_DISCONNECTED;
                g_mnet.status_msg = "Auth timeout";
                return;
            }
            if (g_mnet.has_uuid) {
                len = mnet_encode_connect_uuid(g_mnet.tx_buf, g_mnet.my_uuid);
            } else {
                len = mnet_encode_connect(g_mnet.tx_buf);
            }
            net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
            mnet_log("RETRYING AUTH...");
        }
    }

    g_mnet.heartbeat_counter++;
    if (g_mnet.heartbeat_counter >= MNET_HEARTBEAT_INTERVAL) {
        g_mnet.heartbeat_counter = 0;
        len = mnet_encode_heartbeat(g_mnet.tx_buf);
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
    }
}

/*============================================================================
 * Send functions
 *============================================================================*/

void mnet_send_input_state(uint16_t frame_num, uint8_t input_bits)
{
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;

    if (input_bits != g_mnet.last_sent_input || g_mnet.send_cooldown >= 15) {
        int len = mnet_encode_input_state(g_mnet.tx_buf, frame_num, input_bits);
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
        g_mnet.last_sent_input = input_bits;
        g_mnet.send_cooldown = 0;
    } else {
        g_mnet.send_cooldown++;
    }
    g_mnet.local_frame = frame_num;
}

void mnet_send_input_state_p2(uint16_t frame_num, uint8_t input_bits)
{
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;
    if (g_mnet.my_player_id_2 == MNET_INVALID_PLAYER_ID) return;

    if (input_bits != g_mnet.last_sent_input_p2 || g_mnet.send_cooldown_p2 >= 15) {
        int len = mnet_encode_input_state_p2(g_mnet.tx_buf, g_mnet.my_player_id_2,
                                              frame_num, input_bits);
        net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
        g_mnet.last_sent_input_p2 = input_bits;
        g_mnet.send_cooldown_p2 = 0;
    } else {
        g_mnet.send_cooldown_p2++;
    }
}

void mnet_send_player_state(int16_t x, int16_t y, int16_t z,
                             int16_t ry, int16_t speed,
                             uint8_t lap, uint8_t checkpoint,
                             uint8_t cur_wp, uint16_t dist_wp)
{
    int len;
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;

    /* Throttle to every 4 frames (matches Disasteroids cadence). */
    g_mnet.player_state_cooldown++;
    if (g_mnet.player_state_cooldown < 4) return;
    g_mnet.player_state_cooldown = 0;

    len = mnet_encode_player_state(g_mnet.tx_buf, x, y, z, ry, speed,
                                    lap, checkpoint, cur_wp, dist_wp);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_player_state_p2(int16_t x, int16_t y, int16_t z,
                                int16_t ry, int16_t speed,
                                uint8_t lap, uint8_t checkpoint,
                                uint8_t cur_wp, uint16_t dist_wp)
{
    int len;
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;
    if (g_mnet.my_player_id_2 == MNET_INVALID_PLAYER_ID) return;

    g_mnet.player_state_cooldown_p2++;
    if (g_mnet.player_state_cooldown_p2 < 4) return;
    g_mnet.player_state_cooldown_p2 = 0;

    len = mnet_encode_player_state_p2(g_mnet.tx_buf, g_mnet.my_player_id_2,
                                       x, y, z, ry, speed,
                                       lap, checkpoint, cur_wp, dist_wp);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_lap_complete(uint8_t lap, uint16_t lap_time_secs)
{
    int len;
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;
    len = mnet_encode_lap_complete(g_mnet.tx_buf, lap, lap_time_secs);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_powerup_pickup(uint8_t slot)
{
    int len;
    if (g_mnet.state != MNET_STATE_PLAYING || !g_mnet.transport) return;
    len = mnet_encode_powerup_pickup(g_mnet.tx_buf, slot);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_ready_toggle(void)
{
    int len;
    if (g_mnet.state != MNET_STATE_LOBBY || !g_mnet.transport) return;
    g_mnet.my_ready = !g_mnet.my_ready;
    len = mnet_encode_ready(g_mnet.tx_buf);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_request_start_game(void)
{
    int len;
    if (g_mnet.state != MNET_STATE_LOBBY || !g_mnet.transport) return;
    len = mnet_encode_start_game(g_mnet.tx_buf);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_car_select(uint8_t car_id)
{
    int len;
    if (g_mnet.state != MNET_STATE_LOBBY || !g_mnet.transport) return;
    len = mnet_encode_car_select(g_mnet.tx_buf, car_id);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_add_local_player(const char* name)
{
    int len;
    int i;
    if (g_mnet.state != MNET_STATE_LOBBY || !g_mnet.transport) return;
    /* Cache locally too so reconnect can re-register. */
    for (i = 0; i < MNET_MAX_NAME && name[i]; i++) g_mnet.my_name_2[i] = name[i];
    g_mnet.my_name_2[i] = '\0';
    g_mnet.has_local_p2 = (i > 0);
    len = mnet_encode_add_local_player(g_mnet.tx_buf, name);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_remove_local_player(void)
{
    int len;
    if (!g_mnet.transport) return;
    if (g_mnet.state != MNET_STATE_LOBBY && g_mnet.state != MNET_STATE_PLAYING) return;
    /* Snap a log line BEFORE we clear the pid so the server sees who left. */
    MNET_LOG_INFO("P2 removed (controller unplugged)");
    g_mnet.has_local_p2 = false;
    g_mnet.my_player_id_2 = MNET_INVALID_PLAYER_ID;
    g_mnet.my_name_2[0] = '\0';
    len = mnet_encode_remove_local_player(g_mnet.tx_buf);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

void mnet_send_disconnect(void)
{
    int len;
    if (!g_mnet.transport) return;
    if (g_mnet.state == MNET_STATE_OFFLINE) return;
    /* Last log line before we tear down — must precede the state flip
     * because mnet_client_log skips when DISCONNECTED. */
    MNET_LOG_INFO("Client requested disconnect");
    len = mnet_encode_disconnect(g_mnet.tx_buf);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
    g_mnet.state = MNET_STATE_DISCONNECTED;
    g_mnet.status_msg = "Disconnected";
}

void mnet_request_leaderboard(void)
{
    int len;
    if (g_mnet.state != MNET_STATE_LOBBY || !g_mnet.transport) return;
    len = mnet_encode_leaderboard_req(g_mnet.tx_buf);
    net_transport_send(g_mnet.transport, g_mnet.tx_buf, len);
}

/*============================================================================
 * Lookups
 *============================================================================*/

int mnet_get_remote_input(uint8_t player_id)
{
    int i, best;
    uint16_t best_frame;
    if (player_id >= MNET_MAX_PLAYERS) return -1;

    best = -1;
    best_frame = 0;
    for (i = 0; i < MNET_INPUT_BUFFER_PER_PLAYER; i++) {
        if (!g_mnet.remote_inputs[player_id][i].valid) continue;
        if (best < 0 || g_mnet.remote_inputs[player_id][i].frame_num > best_frame) {
            best_frame = g_mnet.remote_inputs[player_id][i].frame_num;
            best = (int)g_mnet.remote_inputs[player_id][i].input_bits;
        }
    }
    return best;
}

const mnet_remote_state_t* mnet_get_remote_state(uint8_t player_id)
{
    if (player_id >= MNET_MAX_PLAYERS) return (void*)0;
    if (!g_mnet.remote_states[player_id].valid) return (void*)0;
    return &g_mnet.remote_states[player_id];
}

uint8_t mnet_get_powerup_type(uint8_t slot)
{
    if (slot >= MNET_POWERUP_SLOTS) return 0xFF;
    return g_mnet.powerup_types[slot];
}
