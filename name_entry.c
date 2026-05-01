/**
 * name_entry.c - Name entry screen for MMM online play
 *
 * Adapted from Disasteroids' name_entry.c. Restyled to use jo_nbg2_printf()
 * (the same call MMM uses for HUD text) — no drawLetter polygon font.
 *
 * 8-char max name (matches Disasteroids "DISAST_NAME" / Utenyaa "UTEN_NAME"
 * backup-RAM convention). Persists to MMM_NAME on Saturn internal backup.
 */

#include <jo/jo.h>
#include <string.h>
#include "state.h"
#include "name_entry.h"

#define MMM_NAME_MAX     8
#define MMM_NAME_BKP_KEY "MMM_NAME"

/*============================================================================
 * Character grid
 *
 *   Row 0: A B C D E F G H I    (9)
 *   Row 1: J K L M N O P Q R    (9)
 *   Row 2: S T U V W X Y Z      (8)
 *   Row 3: 0 1 2 3 4 5 6 7 8 9  (10)
 *   Row 4: . :  DEL  OK         (4)
 *============================================================================*/

#define GRID_ROWS 5

static const char* g_grid_chars[] = {
    "ABCDEFGHI",
    "JKLMNOPQR",
    "STUVWXYZ",
    "0123456789",
    (char*)0
};

#define ROW4_COUNT 4
#define ROW4_DEL   2
#define ROW4_OK    3

static int g_row_lengths[] = { 9, 9, 8, 10, ROW4_COUNT };

static char g_name_buf[MMM_NAME_MAX + 1];
static int  g_name_len;
static int  g_cursor_row;
static int  g_cursor_col;
static int  g_bkp_load_status;
static bool g_initialized;
static bool g_pressed_AC;
static bool g_pressed_B;
static bool g_pressed_up;
static bool g_pressed_down;
static bool g_pressed_left;
static bool g_pressed_right;
static bool g_pressed_start;

#define KEY_PRESS_PORT(id, key)  ((Smpc_Peripheral[id].data & key) == 0)
#define KEY_DOWN_PORT(id, key)   ((Smpc_Peripheral[id].push & key) == 0)

static int getRowLen(int row)
{
    if (row < 0 || row >= GRID_ROWS) return 0;
    return g_row_lengths[row];
}

static char getGridChar(int row, int col)
{
    if (row < 0 || row >= GRID_ROWS) return 0;
    if (col < 0 || col >= getRowLen(row)) return 0;
    if (row < 4) return g_grid_chars[row][col];
    if (col == 0) return '.';
    if (col == 1) return ':';
    return 0;  /* DEL/OK = actions */
}

static void load_saved_name(void)
{
    g_bkp_load_status = 0;
    if (g_player_name[0] != '\0') return;  /* already set in this session */

    if (jo_backup_mount(JoInternalMemoryBackup)) {
        if (jo_backup_file_exists(JoInternalMemoryBackup, MMM_NAME_BKP_KEY)) {
            unsigned int blen = 0;
            void* data = jo_backup_load_file_contents(
                JoInternalMemoryBackup, MMM_NAME_BKP_KEY, &blen);
            if (data && blen > 0 && blen <= MMM_NAME_MAX + 1) {
                memcpy(g_player_name, data, blen);
                g_player_name[MMM_NAME_MAX] = '\0';
                g_bkp_load_status = 1;
            } else {
                g_bkp_load_status = 4;
            }
            if (data) jo_free(data);
        } else {
            g_bkp_load_status = 3;
        }
        jo_backup_unmount(JoInternalMemoryBackup);
    } else {
        g_bkp_load_status = 2;
    }
}

static void save_name_to_backup(void)
{
    static char bkp_fname[] = MMM_NAME_BKP_KEY;
    static char bkp_comment[] = "MMM Name";
    if (jo_backup_mount(JoInternalMemoryBackup)) {
        jo_backup_save_file_contents(
            JoInternalMemoryBackup, bkp_fname, bkp_comment,
            g_player_name, (unsigned int)(g_name_len + 1));
        jo_backup_unmount(JoInternalMemoryBackup);
    }
}

void name_entry_init(void)
{
    g_initialized = false;
}

static void do_init(void)
{
    int i;

    load_saved_name();

    if (g_player_name[0] != '\0') {
        g_name_len = 0;
        for (i = 0; g_player_name[i] && i < MMM_NAME_MAX; i++) {
            g_name_buf[i] = g_player_name[i];
            g_name_len++;
        }
        g_name_buf[g_name_len] = '\0';
    } else {
        g_name_buf[0] = '\0';
        g_name_len = 0;
    }
    g_cursor_row = 0;
    g_cursor_col = 0;

    /* Force all edge-detect flags pressed so a held button from the title
     * screen doesn't immediately enter a character. */
    g_pressed_AC = true;
    g_pressed_B = true;
    g_pressed_up = true;
    g_pressed_down = true;
    g_pressed_left = true;
    g_pressed_right = true;
    g_pressed_start = true;

    g_initialized = true;
}

static void confirm_name(void)
{
    int i;
    if (g_name_len <= 0) return;

    for (i = 0; i < g_name_len; i++) g_player_name[i] = g_name_buf[i];
    g_player_name[g_name_len] = '\0';

    save_name_to_backup();

    /* Auto-generate P2 name = name + "2" (truncate if at limit). */
    {
        int p2len = g_name_len;
        for (i = 0; i < p2len && i < MMM_NAME_MAX; i++)
            g_player_name_2[i] = g_name_buf[i];
        if (p2len < MMM_NAME_MAX) {
            g_player_name_2[p2len] = '2';
            g_player_name_2[p2len + 1] = '\0';
        } else {
            g_player_name_2[MMM_NAME_MAX - 1] = '2';
            g_player_name_2[MMM_NAME_MAX] = '\0';
        }
    }

    g_online_mode = true;
    mmm_set_game_state(GAMESTATE_CONNECTING);
}

static void handle_input(void)
{
    int rowLen;

    /* D-pad */
    if (KEY_PRESS_PORT(0, PER_DGT_KU)) {
        if (!g_pressed_up) {
            g_cursor_row--;
            if (g_cursor_row < 0) g_cursor_row = GRID_ROWS - 1;
            rowLen = getRowLen(g_cursor_row);
            if (g_cursor_col >= rowLen) g_cursor_col = rowLen - 1;
        }
        g_pressed_up = true;
    } else { g_pressed_up = false; }

    if (KEY_PRESS_PORT(0, PER_DGT_KD)) {
        if (!g_pressed_down) {
            g_cursor_row++;
            if (g_cursor_row >= GRID_ROWS) g_cursor_row = 0;
            rowLen = getRowLen(g_cursor_row);
            if (g_cursor_col >= rowLen) g_cursor_col = rowLen - 1;
        }
        g_pressed_down = true;
    } else { g_pressed_down = false; }

    if (KEY_PRESS_PORT(0, PER_DGT_KL)) {
        if (!g_pressed_left) {
            g_cursor_col--;
            if (g_cursor_col < 0) g_cursor_col = getRowLen(g_cursor_row) - 1;
        }
        g_pressed_left = true;
    } else { g_pressed_left = false; }

    if (KEY_PRESS_PORT(0, PER_DGT_KR)) {
        if (!g_pressed_right) {
            g_cursor_col++;
            if (g_cursor_col >= getRowLen(g_cursor_row)) g_cursor_col = 0;
        }
        g_pressed_right = true;
    } else { g_pressed_right = false; }

    /* A or C select. PER_DGT_TA = A, PER_DGT_TC = C in jo's mapping. */
    if (KEY_PRESS_PORT(0, PER_DGT_TA) || KEY_PRESS_PORT(0, PER_DGT_TC)) {
        if (!g_pressed_AC) {
            if (g_cursor_row == 4 && g_cursor_col == ROW4_DEL) {
                if (g_name_len > 0) {
                    g_name_len--;
                    g_name_buf[g_name_len] = '\0';
                }
            } else if (g_cursor_row == 4 && g_cursor_col == ROW4_OK) {
                confirm_name();
            } else {
                char ch = getGridChar(g_cursor_row, g_cursor_col);
                if (ch != 0 && g_name_len < MMM_NAME_MAX) {
                    g_name_buf[g_name_len++] = ch;
                    g_name_buf[g_name_len] = '\0';
                }
            }
        }
        g_pressed_AC = true;
    } else { g_pressed_AC = false; }

    /* B = back to title */
    if (KEY_PRESS_PORT(0, PER_DGT_TB)) {
        if (!g_pressed_B) {
            g_online_mode = false;
            transition_to_title_screen();
        }
        g_pressed_B = true;
    } else { g_pressed_B = false; }

    /* Start = shortcut for OK if we have a name */
    if (KEY_PRESS_PORT(0, PER_DGT_ST)) {
        if (!g_pressed_start && g_name_len > 0) confirm_name();
        g_pressed_start = true;
    } else { g_pressed_start = false; }
}

#ifndef GAMESTATE_TITLE_SCREEN
#define GAMESTATE_TITLE_SCREEN 1
#endif

void name_entry_screen(void)
{
    int row;
    int gridX = 6;
    int gridY = 9;

    if (mmm_get_game_state() != GAMESTATE_NAME_ENTRY) {
        g_initialized = false;
        return;
    }

    if (!g_initialized) do_init();

    handle_input();

    /* Title */
    jo_nbg2_printf(13, 4, "ENTER NAME");

    /* Disasteroids/Flicky-style: each row is one space-separated string,
     * '>' left-marker on active row, '-' underline below the selected
     * char. Fits the 40-cell NBG2 width with no clipping; no per-cell
     * brackets, no right-side mirror panel. */
    {
        static const char* row_text[5] = {
            "A B C D E F G H I",
            "J K L M N O P Q R",
            "S T U V W X Y Z",
            "0 1 2 3 4 5 6 7 8 9",
            ".  -  DEL  OK"
        };
        int r;
        for (r = 0; r < 5; r++) {
            jo_nbg2_printf(gridX, gridY + (r * 2), "%-22s", row_text[r]);
        }
    }

    /* '>' marker on left of active row, ' ' on others. */
    for (row = 0; row < GRID_ROWS; row++) {
        int rowY = gridY + (row * 2);
        jo_nbg2_printf(gridX - 2, rowY, (row == g_cursor_row) ? ">" : " ");
    }

    /* '-' underline below the selected character. Each char in rows 0-3
     * occupies 2 cells ('A '). Row 4 widths: '.'=1@0, '-'=1@3, 'DEL'=3@6,
     * 'OK'=2@11 — match the row_text spacing. */
    {
        int rowR;
        for (rowR = 0; rowR < GRID_ROWS; rowR++) {
            jo_nbg2_printf(gridX - 1, gridY + (rowR * 2) + 1,
                "                       ");  /* clear */
        }
        int underlineY = gridY + (g_cursor_row * 2) + 1;
        if (g_cursor_row < 4) {
            int cx = gridX + (g_cursor_col * 2);
            jo_nbg2_printf(cx, underlineY, "-");
        } else {
            static const int row4_off[ROW4_COUNT] = { 0, 3, 6, 11 };
            static const int row4_w[ROW4_COUNT]   = { 1, 1, 3, 2 };
            int i;
            for (i = 0; i < row4_w[g_cursor_col]; i++) {
                jo_nbg2_printf(gridX + row4_off[g_cursor_col] + i,
                               underlineY, "-");
            }
        }
    }

    /* Current entry. */
    jo_nbg2_printf(8, 22, "NAME %-8s", g_name_buf);

    /* Controls hint. '/' IS in font; ':' is NOT - replaced with space. */
    if (g_name_len > 0) {
        jo_nbg2_printf(2, 26, "A/C SELECT  B BACK  ST CONFIRM");
    } else {
        jo_nbg2_printf(2, 26, "A/C SELECT  B CANCEL          ");
    }

    /* P2 hint or backup status */
    if (mmm_get_p2_port() >= 0) {
        jo_nbg2_printf(2, 27, "2P CONTROLLER DETECTED        ");
    } else if (g_bkp_load_status > 0) {
        static const char* bkp_msgs[] = {
            "                              ",
            "SAVE LOADED OK                ",
            "BKP MOUNT FAIL                ",
            "NO SAVE FOUND                 ",
            "BKP READ FAIL                 "
        };
        jo_nbg2_printf(2, 27, "%s", bkp_msgs[g_bkp_load_status]);
    } else {
        jo_nbg2_printf(2, 27, "                              ");
    }
}
