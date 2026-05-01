/**
 * mmm_net.h - MicroMotorMayhem Networking State Machine
 *
 * Saturn + NetLink --phone--> USB Modem --serial--> Bridge --TCP--> Server
 */

#ifndef MMM_NET_H
#define MMM_NET_H

#include <stdint.h>
#include <stdbool.h>
#include "net_transport.h"
#include "mmm_protocol.h"

/*============================================================================
 * Constants
 *============================================================================*/

#define MNET_MAX_PLAYERS         4
#define MNET_MAX_NAME            16
#define MNET_HEARTBEAT_INTERVAL  600
#define MNET_AUTH_TIMEOUT        300
#define MNET_AUTH_MAX_RETRIES    5
#define MNET_MAX_PACKETS_FRAME   24
#define MNET_INPUT_BUFFER_PER_PLAYER 8
#define MNET_LEADERBOARD_MAX     10
#define MNET_POWERUP_SLOTS       8

#define MNET_INVALID_PLAYER_ID   0xFF

/*============================================================================
 * Network state
 *============================================================================*/

typedef enum {
    MNET_STATE_OFFLINE = 0,
    MNET_STATE_CONNECTING,
    MNET_STATE_AUTHENTICATING,
    MNET_STATE_USERNAME,
    MNET_STATE_LOBBY,
    MNET_STATE_PLAYING,
    MNET_STATE_DISCONNECTED,
} mnet_state_t;

/*============================================================================
 * Lobby player info
 *============================================================================*/

typedef struct {
    uint8_t id;
    char    name[MNET_MAX_NAME + 1];
    uint8_t car_id;
    bool    ready;
    bool    is_local;     /* true if same Saturn as us (local-coop slot) */
    bool    active;
} mnet_lobby_player_t;

/*============================================================================
 * Game roster (survives lobby state for results screen)
 *============================================================================*/

typedef struct {
    uint8_t id;
    char    name[MNET_MAX_NAME + 1];
    bool    active;
} mnet_roster_entry_t;

/*============================================================================
 * Leaderboard entry
 *============================================================================*/

typedef struct {
    char     name[MNET_MAX_NAME + 1];
    uint16_t wins;
    uint16_t best_lap;
    uint16_t podiums;
    uint16_t races;
    uint16_t points;
} mnet_leaderboard_entry_t;

/*============================================================================
 * Remote input ring buffer entry
 *============================================================================*/

typedef struct {
    uint16_t frame_num;
    uint8_t  input_bits;
    uint8_t  player_id;
    bool     valid;
} mnet_input_entry_t;

/*============================================================================
 * Remote player physical state (for renderer/passthrough sync)
 *============================================================================*/

typedef struct {
    bool     valid;
    int16_t  x, y, z;
    int16_t  ry;
    int16_t  speed;
    uint8_t  lap;
    uint8_t  checkpoint;
    uint8_t  cur_wp;
    uint16_t dist_wp;
    uint16_t last_sync_frame;
} mnet_remote_state_t;

/*============================================================================
 * Race results entry (from RACE_FINISH)
 *============================================================================*/

typedef struct {
    uint8_t  player_id;
    uint8_t  position;
    uint16_t total_time;
    bool     active;
} mnet_finish_entry_t;

/*============================================================================
 * Network state — global
 *============================================================================*/

typedef struct {
    mnet_state_t state;
    bool modem_available;

    const net_transport_t* transport;

    mnet_rx_state_t rx;
    uint8_t rx_buf[MNET_RX_FRAME_SIZE];
    uint8_t tx_buf[MNET_TX_FRAME_SIZE];

    /* Auth */
    char    my_uuid[SNCP_UUID_LEN + 4];
    bool    has_uuid;
    uint8_t my_player_id;
    uint8_t my_player_id_2;     /* 2nd local slot (0xFF = none) */
    int     auth_timer;
    int     auth_retries;
    int     username_retry;

    /* Username */
    char my_name[MNET_MAX_NAME + 1];
    char my_name_2[MNET_MAX_NAME + 1];
    bool has_local_p2;

    /* Lobby */
    mnet_lobby_player_t lobby_players[MNET_MAX_PLAYERS];
    int lobby_count;
    bool my_ready;

    /* Game roster */
    mnet_roster_entry_t game_roster[MNET_MAX_PLAYERS];
    int game_roster_count;

    /* Game config (from GAME_START) */
    uint32_t game_seed;
    uint8_t  track_id;
    uint8_t  lap_count;
    uint8_t  opponent_count;
    bool     game_start_pending;   /* true between GAME_START and consumer ack */

    /* Per-player input ring buffers */
    mnet_input_entry_t remote_inputs[MNET_MAX_PLAYERS][MNET_INPUT_BUFFER_PER_PLAYER];
    int                remote_input_head[MNET_MAX_PLAYERS];

    /* Per-player physical state from server (for lerp/extrap) */
    mnet_remote_state_t remote_states[MNET_MAX_PLAYERS];

    /* Powerup type per slot (from POWERUP_SPAWN). 0xFF = unset. */
    uint8_t powerup_types[MNET_POWERUP_SLOTS];
    /* Powerup spawn positions (server-broadcast; static per-track but server is authoritative) */
    int16_t powerup_x[MNET_POWERUP_SLOTS];
    int16_t powerup_y[MNET_POWERUP_SLOTS];
    int16_t powerup_z[MNET_POWERUP_SLOTS];
    bool    powerup_active[MNET_POWERUP_SLOTS];

    /* Local frame counter for INPUT_STATE */
    uint16_t local_frame;

    /* Send delta state */
    uint8_t  last_sent_input;
    uint16_t send_cooldown;
    uint8_t  last_sent_input_p2;
    uint16_t send_cooldown_p2;
    int      player_state_cooldown;
    int      player_state_cooldown_p2;

    /* Heartbeat */
    int heartbeat_counter;
    int frame_count;

    /* SYNC DIAGNOSTICS — per-pid counters bumped in process_input_relay
     * and process_player_sync, reset on race start. Read by main.c
     * gameplay diagnostic to log per-second receive rates and freshness
     * of remote state, which are the metrics needed to fine-tune sync
     * between online players. */
    uint16_t diag_rx_input_relay[MNET_MAX_PLAYERS];
    uint16_t diag_rx_player_sync[MNET_MAX_PLAYERS];
    uint16_t diag_last_sync_frame[MNET_MAX_PLAYERS]; /* server frame_num from last sync */
    int16_t  diag_last_sync_x[MNET_MAX_PLAYERS];     /* last server-asserted x (for delta calc) */
    int16_t  diag_last_sync_z[MNET_MAX_PLAYERS];     /* last server-asserted z */

    /* Status */
    const char* status_msg;
    int connect_stage;

    /* Logs */
    char log_lines[4][40];
    int  log_count;

    /* Last race results */
    uint8_t last_winner_id;
    bool    has_last_results;
    mnet_finish_entry_t finish[MNET_MAX_PLAYERS];
    int     finish_count;
    bool    race_finished;       /* set on RACE_FINISH; consumed by gameplay */

    /* Server log overlay (for connecting screen) */
    bool    connected_event;     /* true on WELCOME — consumed by lobby */

    /* Online leaderboard */
    mnet_leaderboard_entry_t leaderboard[MNET_LEADERBOARD_MAX];
    int leaderboard_count;

    /* Client-log rate limiter (token bucket). 4 msgs/sec @ 60 fps = +1
     * token per 15 frames, max 4 tokens. Drops silently when empty so
     * we never block the game loop chasing a busy modem. */
    int      client_log_tokens;
    int      client_log_refill;
    uint16_t client_log_dropped;

} mnet_state_data_t;

extern mnet_state_data_t g_mnet;

/*============================================================================
 * Public API
 *============================================================================*/

void mnet_init(void);
void mnet_set_modem_available(bool available);
void mnet_set_transport(const net_transport_t* transport);
void mnet_set_username(const char* name);
void mnet_set_username_2(const char* name);

mnet_state_t mnet_get_state(void);
void mnet_enter_offline(void);

/* Call when modem connection is established. Sends CONNECT and goes AUTH. */
void mnet_on_connected(void);

/* Pump every frame. */
void mnet_tick(void);

/* Send local player input (P1 — primary slot) with delta compression. */
void mnet_send_input_state(uint16_t frame_num, uint8_t input_bits);

/* Same for second local player (only when local_p2_active). */
void mnet_send_input_state_p2(uint16_t frame_num, uint8_t input_bits);

/* Throttled full-state send (every N frames). */
void mnet_send_player_state(int16_t x, int16_t y, int16_t z,
                             int16_t ry, int16_t speed,
                             uint8_t lap, uint8_t checkpoint,
                             uint8_t cur_wp, uint16_t dist_wp);

void mnet_send_player_state_p2(int16_t x, int16_t y, int16_t z,
                                int16_t ry, int16_t speed,
                                uint8_t lap, uint8_t checkpoint,
                                uint8_t cur_wp, uint16_t dist_wp);

void mnet_send_lap_complete(uint8_t lap, uint16_t lap_time_secs);
void mnet_send_powerup_pickup(uint8_t slot);
void mnet_send_ready_toggle(void);
void mnet_request_start_game(void);
void mnet_send_car_select(uint8_t car_id);
void mnet_send_add_local_player(const char* name);
void mnet_send_remove_local_player(void);
void mnet_send_disconnect(void);
void mnet_request_leaderboard(void);

/* Lookup most-recent input bits for a remote player slot (-1 = none). */
int mnet_get_remote_input(uint8_t player_id);

/* Snapshot of a remote player's last server-broadcast state. NULL = not seen yet. */
const mnet_remote_state_t* mnet_get_remote_state(uint8_t player_id);

/* Server-broadcast powerup type for slot. 0xFF = not yet announced. */
uint8_t mnet_get_powerup_type(uint8_t slot);

/* Logs */
void mnet_log(const char* msg);
void mnet_clear_log(void);

/* Send a diagnostic log line UP to the server for remote troubleshooting.
 * Silent no-op when not yet AUTHENTICATING (we have no transport yet) or
 * when the rate-limit bucket is empty. Text truncated to
 * MNET_CLIENT_LOG_TEXT_MAX (80) chars. Also mirrored locally via mnet_log. */
void mnet_client_log(uint8_t level, const char* text);

/* Convenience macros — only emit if connected, no-op otherwise. */
#define MNET_LOG_DEBUG(msg) mnet_client_log(MNET_LOG_LEVEL_DEBUG, msg)
#define MNET_LOG_INFO(msg)  mnet_client_log(MNET_LOG_LEVEL_INFO,  msg)
#define MNET_LOG_WARN(msg)  mnet_client_log(MNET_LOG_LEVEL_WARN,  msg)
#define MNET_LOG_ERROR(msg) mnet_client_log(MNET_LOG_LEVEL_ERROR, msg)

#endif /* MMM_NET_H */
