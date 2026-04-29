/**
 * lobby.c - Online lobby for MMM
 *
 * Adapted from Disasteroids' lobby.c. Restyled with MMM jo_nbg2_printf
 * (no drawLetter/3D-font). Server picks a random track per match — no
 * vote UI; we just display "NEXT TRACK: RANDOM".
 */

#include <jo/jo.h>
#include <string.h>
#include "state.h"
#include "lobby.h"
#include "net/mmm_net.h"

#define KEY_PRESS_PORT(id, key)  ((Smpc_Peripheral[id].data & key) == 0)

#ifndef GAMESTATE_TITLE_SCREEN
#define GAMESTATE_TITLE_SCREEN 1
#endif
#ifndef GAMESTATE_RACE_START
#define GAMESTATE_RACE_START 12
#endif

/*============================================================================
 * Local input edge state
 *============================================================================*/

static bool g_pressed_AC = false;
static bool g_pressed_start = false;
static bool g_pressed_B = false;
static bool g_pressed_Y = false;
static bool g_pressed_L = false;
static bool g_pressed_R = false;
static bool g_pressed_Z = false;
static bool g_z_held = false;
static bool g_z_was_held = false;
static int  g_z_page = 0;
static int  g_z_page_timer = 0;
#define Z_PAGE_INTERVAL 180

static bool g_initialized = false;
static int  g_local_car_id = 0;   /* tracks local car selection between toggles */
#define MMM_CAR_COUNT 8           /* matches default_colour[8][12] in hamster.h */

void lobby_init(void)
{
    g_initialized = false;
}

static void do_init(void)
{
    int row;

    g_pressed_AC = true;   /* prevent button-carry from connecting screen */
    g_pressed_start = true;
    g_pressed_B = true;
    g_pressed_Y = true;
    g_pressed_L = true;
    g_pressed_R = true;
    g_pressed_Z = true;
    g_z_held = false;
    g_z_was_held = false;
    g_z_page = 0;
    g_z_page_timer = 0;

    mnet_clear_log();

    /* Blank rows that may contain stale text */
    for (row = 4; row <= 27; row++) {
        jo_nbg2_printf(0, row, "                                        ");
    }

    mnet_request_leaderboard();

    g_initialized = true;
}

static void poll_local_p2(void)
{
    int p2 = mmm_get_p2_port();

    if (!g_local_p2_active && p2 >= 0) {
        /* Plug edge */
        g_local_p2_active = true;
        if (g_player_name_2[0] == '\0') {
            int i, p1len = 0;
            while (g_player_name[p1len] && p1len < 7) p1len++;
            for (i = 0; i < p1len; i++) g_player_name_2[i] = g_player_name[i];
            if (p1len < 7) {
                g_player_name_2[p1len] = '2';
                g_player_name_2[p1len + 1] = '\0';
            } else {
                g_player_name_2[7] = '2';
                g_player_name_2[8] = '\0';
            }
        }
        mnet_send_add_local_player(g_player_name_2);
    } else if (g_local_p2_active && p2 < 0) {
        /* Unplug edge */
        g_local_p2_active = false;
        g_player_name_2[0] = '\0';
        mnet_send_remove_local_player();
    }
}

static void do_input(void)
{
    /* A/C = ready toggle */
    if (KEY_PRESS_PORT(0, PER_DGT_TA) || KEY_PRESS_PORT(0, PER_DGT_TC)) {
        if (!g_pressed_AC) mnet_send_ready_toggle();
        g_pressed_AC = true;
    } else { g_pressed_AC = false; }

    /* START = request start */
    if (KEY_PRESS_PORT(0, PER_DGT_ST)) {
        if (!g_pressed_start) mnet_request_start_game();
        g_pressed_start = true;
    } else { g_pressed_start = false; }

    /* B = back to title (stay connected on server side, but local UI exits).
     * MUST call mmm_back_to_title_screen() — that forwards to
     * transition_to_title_screen() which reloads TITLE.BIN + TITLE.TGA.
     * Without it, xpdata_/MAP_TILESET still hold track data after a race
     * and the title 3D background renders as garbage. */
    if (KEY_PRESS_PORT(0, PER_DGT_TB)) {
        if (!g_pressed_B) {
            g_online_mode = false;
            mmm_back_to_title_screen();
        }
        g_pressed_B = true;
    } else { g_pressed_B = false; }

    /* Y = full disconnect — same title-restore requirement. */
    if (KEY_PRESS_PORT(0, PER_DGT_TY)) {
        if (!g_pressed_Y) {
            mnet_send_disconnect();
            g_online_mode = false;
            mmm_back_to_title_screen();
        }
        g_pressed_Y = true;
    } else { g_pressed_Y = false; }

    /* L/R = car select */
    if (KEY_PRESS_PORT(0, PER_DGT_TL)) {
        if (!g_pressed_L) {
            g_local_car_id = (g_local_car_id + MMM_CAR_COUNT - 1) % MMM_CAR_COUNT;
            mnet_send_car_select((uint8_t)g_local_car_id);
        }
        g_pressed_L = true;
    } else { g_pressed_L = false; }

    if (KEY_PRESS_PORT(0, PER_DGT_TR)) {
        if (!g_pressed_R) {
            g_local_car_id = (g_local_car_id + 1) % MMM_CAR_COUNT;
            mnet_send_car_select((uint8_t)g_local_car_id);
        }
        g_pressed_R = true;
    } else { g_pressed_R = false; }

    /* Z = held leaderboard overlay */
    g_z_held = KEY_PRESS_PORT(0, PER_DGT_TZ) ? true : false;
}

static void draw_z_overlay(void)
{
    int i;

    g_z_page_timer++;
    if (g_z_page_timer >= Z_PAGE_INTERVAL) {
        g_z_page_timer = 0;
        g_z_page = 1 - g_z_page;
    }

    /* Clear panel area */
    for (i = 8; i <= 22; i++) {
        jo_nbg2_printf(0, i, "                                        ");
    }

    if (g_z_page == 0 && g_mnet.has_last_results) {
        jo_nbg2_printf(8, 8, "LAST RACE RESULTS");
        jo_nbg2_printf(2, 9,  "POS NAME           TIME");
        for (i = 0; i < g_mnet.finish_count && i < MNET_MAX_PLAYERS; i++) {
            if (!g_mnet.finish[i].active) continue;
            {
                uint8_t pid = g_mnet.finish[i].player_id;
                const char* nm = "???";
                int j;
                for (j = 0; j < g_mnet.game_roster_count; j++) {
                    if (g_mnet.game_roster[j].active &&
                        g_mnet.game_roster[j].id == pid) {
                        nm = g_mnet.game_roster[j].name;
                        break;
                    }
                }
                jo_nbg2_printf(2, 10 + i, "%-3d %-14s %4d  ",
                    g_mnet.finish[i].position, nm,
                    g_mnet.finish[i].total_time);
            }
        }
    } else {
        if (g_mnet.leaderboard_count > 0) {
            jo_nbg2_printf(8, 8, "ONLINE LEADERBOARD");
            jo_nbg2_printf(1, 9, "# NAME       W BL POD R PT");
            for (i = 0; i < g_mnet.leaderboard_count && i < 10; i++) {
                jo_nbg2_printf(1, 10 + i,
                    "%-2d %-10s %2d %2d %3d %2d %2d ",
                    i + 1, g_mnet.leaderboard[i].name,
                    g_mnet.leaderboard[i].wins,
                    g_mnet.leaderboard[i].best_lap,
                    g_mnet.leaderboard[i].podiums,
                    g_mnet.leaderboard[i].races,
                    g_mnet.leaderboard[i].points);
            }
        } else {
            jo_nbg2_printf(8, 8, "ONLINE LEADERBOARD");
            jo_nbg2_printf(11, 14, "NO DATA YET");
        }
    }

    if (g_mnet.has_last_results) {
        jo_nbg2_printf(2, 22, "Z %s", g_z_page == 0 ? "RESULTS  " : "LEADERS  ");
    } else {
        jo_nbg2_printf(2, 22, "Z LEADERS  ");
    }
}

static void clear_z_overlay(void)
{
    int i;
    for (i = 8; i <= 22; i++) {
        jo_nbg2_printf(0, i, "                                        ");
    }
}

static void draw_player_rows(void)
{
    int i;

    /* Draw active rows */
    for (i = 0; i < g_mnet.lobby_count && i < MNET_MAX_PLAYERS; i++) {
        const mnet_lobby_player_t* lp = &g_mnet.lobby_players[i];
        const char* ready_str = lp->ready ? "READY" : "---  ";
        const char* loc_tag = lp->is_local ? "P2" : "  ";
        jo_nbg2_printf(2, 10 + i, "%d %-8s CAR%d %s %s    ",
            lp->id, lp->name, lp->car_id, ready_str, loc_tag);
    }
    /* Clear remaining rows */
    for (; i < MNET_MAX_PLAYERS; i++) {
        jo_nbg2_printf(2, 10 + i, "                                ");
    }
}

void lobby_screen(void)
{
    if (mmm_get_game_state() != GAMESTATE_LOBBY) {
        g_initialized = false;
        return;
    }

    if (!g_initialized) do_init();

    do_input();
    poll_local_p2();

    /* GAME_START transition: server fired, set up race + jump to race start. */
    if (g_mnet.state == MNET_STATE_PLAYING && g_mnet.game_start_pending) {
        g_mnet.game_start_pending = false;
        g_online_mode = true;
        mmm_online_start_race();
        return;
    }

    /* Lost connection */
    if (g_mnet.state == MNET_STATE_DISCONNECTED) {
        g_online_mode = false;
        mmm_back_to_title_screen();
        return;
    }

    /* Title */
    jo_nbg2_printf(15, 4, "LOBBY");

    /* Player count */
    jo_nbg2_printf(2, 7, "PLAYERS %d/%-2d", g_mnet.lobby_count, MNET_MAX_PLAYERS);

    /* Z overlay or roster */
    if (g_z_held) {
        draw_z_overlay();
        g_z_was_held = true;
    } else {
        if (g_z_was_held) {
            clear_z_overlay();
            g_z_was_held = false;
            g_z_page = 0;
            g_z_page_timer = 0;
        }
        draw_player_rows();
        /* Centerpiece track-preview placeholder. We don't load the preview
         * TGA in lobby (server hasn't picked yet) — show banner instead. */
        jo_nbg2_printf(11, 16, "NEXT TRACK - RANDOM");
        jo_nbg2_printf(2, 18, "YOUR CAR %d", g_local_car_id);

        if (g_mnet.lobby_count < 2) {
            jo_nbg2_printf(7, 20, "WAITING FOR PLAYERS...");
        } else {
            jo_nbg2_printf(7, 20, "                       ");
        }

        /* Last log line */
        if (g_mnet.log_count > 0) {
            jo_nbg2_printf(2, 22, "%-35s",
                g_mnet.log_lines[g_mnet.log_count - 1]);
        } else {
            jo_nbg2_printf(2, 22, "                                   ");
        }

        /* P2 status */
        if (g_local_p2_active) {
            jo_nbg2_printf(2, 23, "P2 %-8s", g_player_name_2);
        } else {
            jo_nbg2_printf(2, 23, "                ");
        }
    }

    /* Controls hint */
    jo_nbg2_printf(1, 26, "A:RDY  L/R:CAR  ST:GO  B:BACK ");
    jo_nbg2_printf(1, 27, "Y:DISCON  Z:STATS              ");
}
