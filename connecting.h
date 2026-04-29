/**
 * connecting.h - Modem connect flow for MMM online play
 */

#ifndef MMM_CONNECTING_H
#define MMM_CONNECTING_H

void connecting_init(void);
void connecting_screen(void);   /* Per-frame entry; returns immediately if not in this state */

#endif
