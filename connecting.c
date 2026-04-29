/**
 * connecting.c - Connection screen for MMM online play
 *
 * Adapted from Disasteroids' connecting.c. Frame-by-frame state machine
 * so status text renders between blocking modem calls (slSynch() forces
 * a frame between display and dial).
 */

#include <jo/jo.h>
#include "state.h"
#include "connecting.h"
#include "net/mmm_net.h"
#include "net/saturn_uart16550.h"
#include "net/modem.h"

#define CONNECT_DIAL_NUMBER   "199406"
#define CONNECT_DIAL_TIMEOUT  180000000  /* ~60s @ 28.6MHz */

extern void slSynch(void);

extern saturn_uart16550_t g_uart;
extern bool g_modem_detected;
extern net_transport_t g_saturn_transport;

#define KEY_PRESS_PORT(id, key)  ((Smpc_Peripheral[id].data & key) == 0)

typedef enum {
    CONNECT_STAGE_INIT = 0,
    CONNECT_STAGE_SHOW_PROBE,
    CONNECT_STAGE_PROBING,
    CONNECT_STAGE_SHOW_INIT,
    CONNECT_STAGE_MODEM_INIT,
    CONNECT_STAGE_SHOW_DIAL,
    CONNECT_STAGE_DIALING,
    CONNECT_STAGE_CONNECTED,
    CONNECT_STAGE_FAILED,
} connect_stage_t;

static connect_stage_t g_stage;
static const char* g_msg = "";
static int g_timer = 0;
static bool g_initialized = false;
static bool g_pressed_B = false;

#ifndef GAMESTATE_TITLE_SCREEN
#define GAMESTATE_TITLE_SCREEN 1
#endif

void connecting_init(void)
{
    g_initialized = false;
}

static void do_init(void)
{
    g_stage = CONNECT_STAGE_INIT;
    g_msg = "PREPARING...";
    g_timer = 0;
    g_pressed_B = true;

    mnet_init();
    mnet_set_modem_available(g_modem_detected);
    mnet_set_username(g_player_name[0] ? g_player_name : "PLAYER");

    /* Re-detect P2 (may have been plugged after name entry). If we don't
     * have a P2 name yet, derive it from P1 + "2". */
    if (mmm_get_p2_port() >= 0) {
        if (g_player_name_2[0] == '\0' && g_player_name[0] != '\0') {
            int j, p1len = 0;
            while (g_player_name[p1len] && p1len < 7) p1len++;
            for (j = 0; j < p1len; j++) g_player_name_2[j] = g_player_name[j];
            if (p1len < 7) {
                g_player_name_2[p1len] = '2';
                g_player_name_2[p1len + 1] = '\0';
            } else {
                g_player_name_2[7] = '2';
                g_player_name_2[8] = '\0';
            }
        }
        g_local_p2_active = true;
        if (g_player_name_2[0] != '\0') mnet_set_username_2(g_player_name_2);
    } else {
        g_local_p2_active = false;
    }
    g_initialized = true;
}

static void do_input(void)
{
    if (KEY_PRESS_PORT(0, PER_DGT_TB)) {
        if (!g_pressed_B) {
            mnet_send_disconnect();
            g_online_mode = false;
            mmm_set_game_state(GAMESTATE_TITLE_SCREEN);
        }
        g_pressed_B = true;
    } else {
        g_pressed_B = false;
    }
}

static void advance_stage(void)
{
    modem_result_t result;

    switch (g_stage) {
    case CONNECT_STAGE_INIT:
        if (!g_modem_detected) {
            g_msg = "NO NETLINK MODEM";
            mnet_log("No NetLink modem");
            g_stage = CONNECT_STAGE_FAILED;
            return;
        }
        g_stage = CONNECT_STAGE_SHOW_PROBE;
        break;

    case CONNECT_STAGE_SHOW_PROBE:
        g_msg = "PROBING MODEM...";
        mnet_log("Probing modem...");
        g_stage = CONNECT_STAGE_PROBING;
        break;

    case CONNECT_STAGE_PROBING:
        slSynch();
        if (modem_probe(&g_uart) != MODEM_OK) {
            g_msg = "NO MODEM RESPONSE";
            mnet_log("No modem response");
            g_stage = CONNECT_STAGE_FAILED;
            return;
        }
        mnet_log("Modem detected");
        g_stage = CONNECT_STAGE_SHOW_INIT;
        break;

    case CONNECT_STAGE_SHOW_INIT:
        g_msg = "INITIALIZING MODEM...";
        mnet_log("Initializing modem...");
        g_stage = CONNECT_STAGE_MODEM_INIT;
        break;

    case CONNECT_STAGE_MODEM_INIT:
        slSynch();
        if (modem_init(&g_uart) != MODEM_OK) {
            g_msg = "MODEM INIT FAILED";
            mnet_log("Modem init failed");
            g_stage = CONNECT_STAGE_FAILED;
            return;
        }
        mnet_log("Modem ready");
        g_stage = CONNECT_STAGE_SHOW_DIAL;
        break;

    case CONNECT_STAGE_SHOW_DIAL:
        g_msg = "DIALING SERVER...";
        mnet_log("Dialing " CONNECT_DIAL_NUMBER "...");
        g_stage = CONNECT_STAGE_DIALING;
        break;

    case CONNECT_STAGE_DIALING:
        slSynch();
        result = modem_dial(&g_uart, CONNECT_DIAL_NUMBER, CONNECT_DIAL_TIMEOUT);
        switch (result) {
        case MODEM_CONNECT:
            g_msg = "CONNECTED!";
            mnet_log("Connection established");
            modem_flush_input(&g_uart);
            g_stage = CONNECT_STAGE_CONNECTED;
            break;
        case MODEM_NO_CARRIER:
            g_msg = "NO CARRIER";
            mnet_log("NO CARRIER - check cable");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        case MODEM_BUSY:
            g_msg = "LINE BUSY";
            mnet_log("LINE BUSY - try again");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        case MODEM_NO_DIALTONE:
            g_msg = "NO DIALTONE";
            mnet_log("NO DIALTONE - check line");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        case MODEM_NO_ANSWER:
            g_msg = "NO ANSWER";
            mnet_log("NO ANSWER - server down?");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        case MODEM_TIMEOUT_ERR:
            g_msg = "TIMEOUT";
            mnet_log("TIMEOUT - server offline?");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        default:
            g_msg = "UNKNOWN ERROR";
            mnet_log("Dial failed");
            g_stage = CONNECT_STAGE_FAILED;
            break;
        }
        break;

    case CONNECT_STAGE_CONNECTED:
        saturn_uart_reg_write(&g_uart, SATURN_UART_FCR,
            SATURN_UART_FCR_ENABLE | SATURN_UART_FCR_RXRESET);
        mnet_set_transport(&g_saturn_transport);
        mnet_on_connected();
        mmm_set_game_state(GAMESTATE_LOBBY);
        break;

    case CONNECT_STAGE_FAILED:
        g_timer++;
        if (g_timer > 180) {
            g_online_mode = false;
            mmm_set_game_state(GAMESTATE_TITLE_SCREEN);
        }
        break;
    }
}

void connecting_screen(void)
{
    int i;

    if (mmm_get_game_state() != GAMESTATE_CONNECTING) {
        g_initialized = false;
        return;
    }

    if (!g_initialized) do_init();

    do_input();
    advance_stage();

    /* Title */
    jo_nbg2_printf(13, 4, "CONNECTING");

    /* Status (padded to clear stale longer text) */
    jo_nbg2_printf(5, 12, "%-30s", g_msg);

    /* Log lines */
    for (i = 0; i < 4; i++) {
        if (i < g_mnet.log_count) {
            jo_nbg2_printf(3, 16 + i, "%-33s", g_mnet.log_lines[i]);
        } else {
            jo_nbg2_printf(3, 16 + i, "                                 ");
        }
    }

    jo_nbg2_printf(8, 26, "PRESS B TO CANCEL");
}
