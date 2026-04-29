/**
 * mmm_protocol.h - MicroMotorMayhem Network Protocol
 *
 * Binary protocol using SNCP framing for MMM online multiplayer.
 * Sync model: Disasteroids passthrough (client-auth movement,
 * server-auth lap/checkpoint/powerup-roll/finish).
 *
 * Header-only: all functions are static inline.
 */

#ifndef MMM_PROTOCOL_H
#define MMM_PROTOCOL_H

#include <stdint.h>
#include <string.h>
#include "net_transport.h"

/*============================================================================
 * SNCP Auth Messages
 *============================================================================*/

#define SNCP_MSG_CONNECT           0x01
#define SNCP_MSG_SET_USERNAME      0x02
#define SNCP_MSG_HEARTBEAT         0x04
#define SNCP_MSG_DISCONNECT        0x05

#define SNCP_MSG_USERNAME_REQUIRED 0x81
#define SNCP_MSG_WELCOME           0x82
#define SNCP_MSG_WELCOME_BACK      0x83
#define SNCP_MSG_USERNAME_TAKEN    0x84

#define SNCP_UUID_LEN              36

/*============================================================================
 * MMM Client -> Server Messages (0x10 - 0x1F)
 *============================================================================*/

#define MNET_MSG_READY              0x10  /* Toggle ready (no payload) */
#define MNET_MSG_INPUT_STATE        0x11  /* [frame:2 BE][input:1] */
#define MNET_MSG_START_GAME_REQ     0x12  /* Request game start */
#define MNET_MSG_CAR_SELECT         0x13  /* [car_id:1] */
#define MNET_MSG_PLAYER_STATE       0x14  /* [x:2s][y:2s][z:2s][ry:2s][speed:2s][lap:1][cp:1][cur_wp:1][dist_wp:2 BE] */
#define MNET_MSG_POWERUP_PICKUP     0x15  /* [slot:1] */
#define MNET_MSG_ADD_LOCAL_PLAYER   0x16  /* [name_len:1][name:N] */
#define MNET_MSG_REMOVE_LOCAL_PLAYER 0x17 /* (no payload) */
#define MNET_MSG_LEADERBOARD_REQ    0x18  /* (no payload) */
#define MNET_MSG_LAP_COMPLETE       0x19  /* [lap:1][lap_time_secs:2 BE] advisory */
#define MNET_MSG_HEARTBEAT          0x1A  /* (no payload) */
#define MNET_MSG_CLIENT_LOG         0x1B  /* [level:1][text_len:1][text:N] client-side diagnostic log */

/* Client log severity levels (matches server side ordering). */
#define MNET_LOG_LEVEL_DEBUG   0
#define MNET_LOG_LEVEL_INFO    1
#define MNET_LOG_LEVEL_WARN    2
#define MNET_LOG_LEVEL_ERROR   3

/* Max chars (after type/level/len) of log text — keeps a frame ≤ 84 bytes
 * which fits comfortably in the 14400-baud-friendly TX path. */
#define MNET_CLIENT_LOG_TEXT_MAX  80

/*============================================================================
 * MMM Server -> Client Messages (0xA0 - 0xBF)
 *============================================================================*/

#define MNET_MSG_LOBBY_STATE        0xA0  /* [count:1][{id:1,name:LP,car:1,ready:1,is_local:1}...] */
#define MNET_MSG_GAME_START         0xA1  /* [seed:4 BE][my_player_id:1][opp_count:1][track_id:1][lap_count:1] */
#define MNET_MSG_INPUT_RELAY        0xA2  /* [player_id:1][frame:2 BE][input:1] */
#define MNET_MSG_PLAYER_JOIN        0xA3  /* [id:1][name:LP] */
#define MNET_MSG_PLAYER_LEAVE       0xA4  /* [id:1] */
#define MNET_MSG_GAME_OVER          0xA5  /* [winner_id:1] */
#define MNET_MSG_LOG                0xA6  /* [len:1][text:N] */
#define MNET_MSG_LOCAL_PLAYER_ACK   0x86  /* [player_id:1] */
#define MNET_MSG_PLAYER_SYNC        0xA9  /* [player_id:1][x:2s BE][y:2s BE][z:2s BE][ry:2s BE][speed:2s BE][lap:1][cp:1][cur_wp:1][dist_wp:2 BE] */
#define MNET_MSG_POWERUP_SPAWN      0xAA  /* [slot:1][type:1][x:2 BE][y:2 BE][z:2 BE] */
#define MNET_MSG_POWERUP_DESTROY    0xAB  /* [slot:1][taker_id:1] */
#define MNET_MSG_POWERUP_EFFECT     0xAC  /* [player_id:1][effect:1] */
#define MNET_MSG_LAP_NOTIFY         0xAD  /* [player_id:1][lap:1][position:1] */
#define MNET_MSG_RACE_FINISH        0xAE  /* [winner:1][count:1]{[pid:1][pos:1][total_time:2BE]}... */
#define MNET_MSG_LEADERBOARD_DATA   0xAF  /* [count:1]{[name_len:1][name:N][wins:2BE][best_lap:2BE][podiums:2BE][races:2BE][points:2BE]}... */

/*============================================================================
 * Input State Bitmask (1 byte — fits a racer's controls cleanly)
 *============================================================================*/

#define MNET_INPUT_GAS    (1 << 0)
#define MNET_INPUT_BRAKE  (1 << 1)
#define MNET_INPUT_LEFT   (1 << 2)
#define MNET_INPUT_RIGHT  (1 << 3)
#define MNET_INPUT_ACTION (1 << 4)  /* powerup */
#define MNET_INPUT_START  (1 << 5)
#define MNET_INPUT_HORN   (1 << 6)
/* bit 7 reserved */

/*============================================================================
 * Buffer Sizes
 *============================================================================*/

#define MNET_RX_FRAME_SIZE  512
/* TX buf must fit the largest single outbound frame. Client-log can be
 *   2 hdr + 1 type + 1 level + 1 len + 80 text = 85 bytes. */
#define MNET_TX_FRAME_SIZE  96

/*============================================================================
 * Frame Send/Receive (SNCP framing)
 *============================================================================*/

static inline void mnet_send_frame(const net_transport_t* transport,
                                    const uint8_t* payload, int payload_len)
{
    uint8_t hdr[2];
    hdr[0] = (uint8_t)((payload_len >> 8) & 0xFF);
    hdr[1] = (uint8_t)(payload_len & 0xFF);
    net_transport_send(transport, hdr, 2);
    net_transport_send(transport, payload, payload_len);
}

typedef struct {
    uint8_t* buf;
    int      buf_size;
    int      rx_pos;
    int      frame_len;
} mnet_rx_state_t;

static inline void mnet_rx_init(mnet_rx_state_t* st, uint8_t* buf, int buf_size)
{
    st->buf = buf;
    st->buf_size = buf_size;
    st->rx_pos = 0;
    st->frame_len = -1;
}

/* Bounded poll — same rationale as Disasteroids: keep A-bus stalls short
 * so VDP1 isn't starved. */
#define MNET_RX_MAX_PER_POLL  48

static inline int mnet_rx_poll(mnet_rx_state_t* st,
                                const net_transport_t* transport)
{
    int bytes_read = 0;
    while (bytes_read < MNET_RX_MAX_PER_POLL && net_transport_rx_ready(transport)) {
        uint8_t b = net_transport_rx_byte(transport);
        bytes_read++;

        if (st->frame_len < 0) {
            st->buf[st->rx_pos++] = b;
            if (st->rx_pos == 2) {
                st->frame_len = ((int)st->buf[0] << 8) | (int)st->buf[1];
                st->rx_pos = 0;
                if (st->frame_len > st->buf_size || st->frame_len == 0) {
                    st->frame_len = -1;
                    st->rx_pos = 0;
                    return -1;
                }
            }
        } else {
            st->buf[st->rx_pos++] = b;
            if (st->rx_pos >= st->frame_len) {
                int len = st->frame_len;
                st->frame_len = -1;
                st->rx_pos = 0;
                return len;
            }
        }
    }
    return 0;
}

/*============================================================================
 * Decode helpers
 *============================================================================*/

static inline int mnet_read_string(const uint8_t* p, int remaining,
                                    char* dst, int max)
{
    int slen, copy, i;
    if (remaining < 1) { dst[0] = '\0'; return -1; }
    slen = (int)p[0];
    if (remaining < 1 + slen) { dst[0] = '\0'; return -1; }
    copy = (slen < max - 1) ? slen : (max - 1);
    for (i = 0; i < copy; i++) dst[i] = (char)p[1 + i];
    dst[copy] = '\0';
    return 1 + slen;
}

/*============================================================================
 * Client -> Server encoders
 *============================================================================*/

static inline int mnet_encode_connect(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = SNCP_MSG_CONNECT;
    return 3;
}

static inline int mnet_encode_connect_uuid(uint8_t* buf, const char* uuid)
{
    int i;
    buf[0] = 0x00; buf[1] = 37;
    buf[2] = SNCP_MSG_CONNECT;
    for (i = 0; i < SNCP_UUID_LEN; i++) buf[3 + i] = (uint8_t)uuid[i];
    return 3 + SNCP_UUID_LEN;
}

static inline int mnet_encode_set_username(uint8_t* buf, const char* name)
{
    int nlen = 0;
    int payload_len, i;
    while (name[nlen]) nlen++;
    if (nlen > 16) nlen = 16;
    payload_len = 1 + 1 + nlen;
    buf[0] = (uint8_t)((payload_len >> 8) & 0xFF);
    buf[1] = (uint8_t)(payload_len & 0xFF);
    buf[2] = SNCP_MSG_SET_USERNAME;
    buf[3] = (uint8_t)nlen;
    for (i = 0; i < nlen; i++) buf[4 + i] = (uint8_t)name[i];
    return 2 + payload_len;
}

static inline int mnet_encode_disconnect(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = SNCP_MSG_DISCONNECT;
    return 3;
}

static inline int mnet_encode_heartbeat(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = SNCP_MSG_HEARTBEAT;
    return 3;
}

static inline int mnet_encode_ready(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = MNET_MSG_READY;
    return 3;
}

static inline int mnet_encode_start_game(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = MNET_MSG_START_GAME_REQ;
    return 3;
}

static inline int mnet_encode_car_select(uint8_t* buf, uint8_t car_id)
{
    buf[0] = 0x00; buf[1] = 0x02;
    buf[2] = MNET_MSG_CAR_SELECT;
    buf[3] = car_id;
    return 4;
}

static inline int mnet_encode_input_state(uint8_t* buf,
                                           uint16_t frame_num,
                                           uint8_t input_bits)
{
    /* payload = type(1) + frame(2) + input(1) = 4 */
    buf[0] = 0x00; buf[1] = 0x04;
    buf[2] = MNET_MSG_INPUT_STATE;
    buf[3] = (uint8_t)((frame_num >> 8) & 0xFF);
    buf[4] = (uint8_t)(frame_num & 0xFF);
    buf[5] = input_bits;
    return 6;
}

/* INPUT_STATE with explicit player_id (for second local player). */
static inline int mnet_encode_input_state_p2(uint8_t* buf,
                                              uint8_t player_id,
                                              uint16_t frame_num,
                                              uint8_t input_bits)
{
    /* payload = type(1) + pid(1) + frame(2) + input(1) = 5 */
    buf[0] = 0x00; buf[1] = 0x05;
    buf[2] = MNET_MSG_INPUT_STATE;
    buf[3] = player_id;
    buf[4] = (uint8_t)((frame_num >> 8) & 0xFF);
    buf[5] = (uint8_t)(frame_num & 0xFF);
    buf[6] = input_bits;
    return 7;
}

/* PLAYER_STATE payload (signed int16 BE for spatial fields):
 *   [type:1][x:2][y:2][z:2][ry:2][speed:2][lap:1][cp:1][cur_wp:1][dist_wp:2] = 16 */
static inline int mnet_encode_player_state(uint8_t* buf,
                                            int16_t x, int16_t y, int16_t z,
                                            int16_t ry, int16_t speed,
                                            uint8_t lap, uint8_t checkpoint,
                                            uint8_t cur_wp,
                                            uint16_t dist_wp)
{
    buf[0] = 0x00; buf[1] = 16;
    buf[2] = MNET_MSG_PLAYER_STATE;
    buf[3]  = (uint8_t)((x >> 8) & 0xFF);
    buf[4]  = (uint8_t)(x & 0xFF);
    buf[5]  = (uint8_t)((y >> 8) & 0xFF);
    buf[6]  = (uint8_t)(y & 0xFF);
    buf[7]  = (uint8_t)((z >> 8) & 0xFF);
    buf[8]  = (uint8_t)(z & 0xFF);
    buf[9]  = (uint8_t)((ry >> 8) & 0xFF);
    buf[10] = (uint8_t)(ry & 0xFF);
    buf[11] = (uint8_t)((speed >> 8) & 0xFF);
    buf[12] = (uint8_t)(speed & 0xFF);
    buf[13] = lap;
    buf[14] = checkpoint;
    buf[15] = cur_wp;
    buf[16] = (uint8_t)((dist_wp >> 8) & 0xFF);
    buf[17] = (uint8_t)(dist_wp & 0xFF);
    return 18;
}

/* PLAYER_STATE for second local player.
 * Payload: [type:1][pid:1][x:2][y:2][z:2][ry:2][speed:2][lap:1][cp:1][cur_wp:1][dist_wp:2] = 17 */
static inline int mnet_encode_player_state_p2(uint8_t* buf,
                                               uint8_t player_id,
                                               int16_t x, int16_t y, int16_t z,
                                               int16_t ry, int16_t speed,
                                               uint8_t lap, uint8_t checkpoint,
                                               uint8_t cur_wp,
                                               uint16_t dist_wp)
{
    buf[0] = 0x00; buf[1] = 17;
    buf[2] = MNET_MSG_PLAYER_STATE;
    buf[3] = player_id;
    buf[4]  = (uint8_t)((x >> 8) & 0xFF);
    buf[5]  = (uint8_t)(x & 0xFF);
    buf[6]  = (uint8_t)((y >> 8) & 0xFF);
    buf[7]  = (uint8_t)(y & 0xFF);
    buf[8]  = (uint8_t)((z >> 8) & 0xFF);
    buf[9]  = (uint8_t)(z & 0xFF);
    buf[10] = (uint8_t)((ry >> 8) & 0xFF);
    buf[11] = (uint8_t)(ry & 0xFF);
    buf[12] = (uint8_t)((speed >> 8) & 0xFF);
    buf[13] = (uint8_t)(speed & 0xFF);
    buf[14] = lap;
    buf[15] = checkpoint;
    buf[16] = cur_wp;
    buf[17] = (uint8_t)((dist_wp >> 8) & 0xFF);
    buf[18] = (uint8_t)(dist_wp & 0xFF);
    return 19;
}

static inline int mnet_encode_powerup_pickup(uint8_t* buf, uint8_t slot)
{
    buf[0] = 0x00; buf[1] = 0x02;
    buf[2] = MNET_MSG_POWERUP_PICKUP;
    buf[3] = slot;
    return 4;
}

static inline int mnet_encode_add_local_player(uint8_t* buf, const char* name)
{
    int nlen = 0;
    int payload_len, i;
    while (name[nlen]) nlen++;
    if (nlen > 16) nlen = 16;
    payload_len = 1 + 1 + nlen;
    buf[0] = (uint8_t)((payload_len >> 8) & 0xFF);
    buf[1] = (uint8_t)(payload_len & 0xFF);
    buf[2] = MNET_MSG_ADD_LOCAL_PLAYER;
    buf[3] = (uint8_t)nlen;
    for (i = 0; i < nlen; i++) buf[4 + i] = (uint8_t)name[i];
    return 2 + payload_len;
}

static inline int mnet_encode_remove_local_player(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = MNET_MSG_REMOVE_LOCAL_PLAYER;
    return 3;
}

static inline int mnet_encode_leaderboard_req(uint8_t* buf)
{
    buf[0] = 0x00; buf[1] = 0x01;
    buf[2] = MNET_MSG_LEADERBOARD_REQ;
    return 3;
}

/* CLIENT_LOG payload: [type:1][level:1][text_len:1][text:N]
 * Truncates text at MNET_CLIENT_LOG_TEXT_MAX. NULL text encodes as empty. */
static inline int mnet_encode_client_log(uint8_t* buf,
                                          uint8_t level,
                                          const char* text)
{
    int tlen = 0;
    int payload_len, i;
    if (text) {
        while (text[tlen] && tlen < MNET_CLIENT_LOG_TEXT_MAX) tlen++;
    }
    payload_len = 1 + 1 + 1 + tlen;
    buf[0] = (uint8_t)((payload_len >> 8) & 0xFF);
    buf[1] = (uint8_t)(payload_len & 0xFF);
    buf[2] = MNET_MSG_CLIENT_LOG;
    buf[3] = level;
    buf[4] = (uint8_t)tlen;
    for (i = 0; i < tlen; i++) buf[5 + i] = (uint8_t)text[i];
    return 2 + payload_len;
}

static inline int mnet_encode_lap_complete(uint8_t* buf,
                                            uint8_t lap, uint16_t lap_time)
{
    buf[0] = 0x00; buf[1] = 0x04;
    buf[2] = MNET_MSG_LAP_COMPLETE;
    buf[3] = lap;
    buf[4] = (uint8_t)((lap_time >> 8) & 0xFF);
    buf[5] = (uint8_t)(lap_time & 0xFF);
    return 6;
}

#endif /* MMM_PROTOCOL_H */
