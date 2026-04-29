/**
 * name_entry.h - Name Entry Screen (online play)
 *
 * 8-char max name (matches sibling games' backup-RAM convention).
 * Persists to backup RAM key MMM_NAME.
 */

#ifndef MMM_NAME_ENTRY_H
#define MMM_NAME_ENTRY_H

void name_entry_init(void);
void name_entry_screen(void);   /* Call every frame; returns immediately if not in this state */

#endif
