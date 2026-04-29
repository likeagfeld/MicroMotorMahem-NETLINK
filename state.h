/**
 * state.h - NetLink-related game-state extensions for MMM
 *
 * Defines:
 *   - online-only GAMESTATE / GAMEMODE constants
 *   - online-mode flags placed in extern globals (defined in main.c)
 *   - shared name buffer used by name_entry / connecting / lobby
 *
 * Intentionally minimal. hamster.h is main.c-only (it defines globals at
 * file scope), so screens/net code stay decoupled from it.
 */

#ifndef MMM_STATE_H
#define MMM_STATE_H

#include <stdbool.h>
#include <stdint.h>

/*============================================================================
 * New game states (extend hamster.h's enum range)
 *============================================================================*/

#ifndef GAMESTATE_NAME_ENTRY
#define GAMESTATE_NAME_ENTRY      (13)
#define GAMESTATE_CONNECTING      (14)
#define GAMESTATE_LOBBY           (15)
#endif

/* Shadow of hamster.h's existing constants for screens that don't pull
 * in hamster.h. Keep in sync with hamster.h:58–70 / 72–76. */
#ifndef GAMESTATE_TITLE_SCREEN
#define GAMESTATE_TITLE_SCREEN    (1)
#endif
#ifndef GAMESTATE_GAMEPLAY
#define GAMESTATE_GAMEPLAY        (3)
#endif
#ifndef GAMESTATE_RACE_START
#define GAMESTATE_RACE_START      (12)
#endif
#ifndef GAMESTATE_END_LEVEL
#define GAMESTATE_END_LEVEL       (8)
#endif

/*============================================================================
 * New game mode (extends hamster.h GAMEMODE_*)
 *============================================================================*/

#ifndef GAMEMODE_NETLINKRACE
#define GAMEMODE_NETLINKRACE      (5)
#endif

/*============================================================================
 * Online-mode flags & shared inputs (defined in main.c)
 *============================================================================*/

extern bool      g_online_mode;        /* true while we're in a netlink race flow */
extern bool      g_local_p2_active;    /* 2nd controller plugged + accepted by server */
extern char      g_player_name[17];    /* P1 name, persisted to MMM_NAME backup key */
extern char      g_player_name_2[17];  /* P2 name (= P1 + "2" by default) */

/*============================================================================
 * Game state poke — main.c implements these so screens can transition without
 * pulling in the full game struct.
 *============================================================================*/

void mmm_set_game_state(uint8_t new_state);
uint8_t mmm_get_game_state(void);

/*============================================================================
 * Returns Smpc port index of P2 controller, or -1 if absent.
 *============================================================================*/
int mmm_get_p2_port(void);

/*============================================================================
 * Online-race setup: called by lobby once GAME_START arrives. Loads track
 * from server's track_id, builds players[] from server roster, kicks off
 * GAMESTATE_RACE_START. Implemented in main.c (where hamster.h is in scope).
 *============================================================================*/
void mmm_online_start_race(void);

/*============================================================================
 * Mark a player slot's online attributes. p in [0..3].
 *============================================================================*/
void mmm_set_player_net_info(int p, bool is_local, uint8_t net_id, bool is_bot);

#endif /* MMM_STATE_H */
