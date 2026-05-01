JO_COMPILE_WITH_VIDEO_MODULE = 0
JO_COMPILE_WITH_BACKUP_MODULE = 1
JO_COMPILE_WITH_TGA_MODULE = 1
JO_COMPILE_WITH_AUDIO_MODULE = 0
JO_COMPILE_WITH_3D_MODULE = 1
JO_COMPILE_WITH_PSEUDO_MODE7_MODULE = 0
JO_COMPILE_WITH_EFFECTS_MODULE = 0
JO_COMPILE_WITH_PRINTF_MODULE = 0
JO_DEBUG = 0
JO_COMPILE_WITH_FAST_BUT_LESS_ACCURATE_MATH = 0
JO_COMPILE_USING_SGL=1
SRCS=main.c collision.c pcmsys.c name_entry.c connecting.c lobby.c net/mmm_net.c libc_stubs.c
JO_ENGINE_SRC_DIR=../../jo_engine
COMPILER_DIR=../../Compiler
include $(COMPILER_DIR)/COMMON/jo_engine_makefile
# Optimize for size — -O2 produced 326 KB binaries that black-screened
# on cold boot; the alpha-0.4 user-tested binary at 316 KB worked. -Os
# trades a small amount of runtime perf for ~10 KB binary shrinkage,
# bringing us back under the apparent ~317 KB boot threshold on the
# user's hardware (+ giving headroom for the new fixes).
CCFLAGS += -Os
