/*
 * libc_stubs.c — route newlib's malloc family into jo_engine's pool
 *
 * Same defense-in-depth wrapper Utenyaa uses: by exporting malloc/free/
 * _malloc_r/_free_r/_calloc_r/_realloc_r from this TU before the libc
 * archive is consulted, the static link picks our symbols up first and
 * skips newlib's malloc.o entirely. Newlib's printf/string code still
 * works (we still want memcpy, vsnprintf, etc.) but every allocation
 * routes to jo_engine's static pool — preventing cross-allocator frees
 * which crash with "jo_free Bad pointer" on hardware.
 *
 * _sbrk hard-fails: if any stray reference to newlib's stock allocator
 * survives, we'd rather it return -1 than scribble random RAM.
 */

#include <stddef.h>
#include <jo/jo.h>

void *malloc(size_t size)
{
    return jo_malloc((unsigned int)size);
}

void free(void *ptr)
{
    if (ptr) jo_free(ptr);
}

void *calloc(size_t nmemb, size_t size)
{
    unsigned int total = (unsigned int)(nmemb * size);
    void *p = jo_malloc(total);
    if (p) {
        unsigned char *b = (unsigned char *)p;
        unsigned int i;
        for (i = 0; i < total; i++) b[i] = 0;
    }
    return p;
}

void *realloc(void *ptr, size_t size)
{
    if (!ptr) return jo_malloc((unsigned int)size);
    if (size == 0) { jo_free(ptr); return NULL; }
    {
        void *np = jo_malloc((unsigned int)size);
        if (!np) return NULL;
        {
            unsigned char *src = (unsigned char *)ptr;
            unsigned char *dst = (unsigned char *)np;
            unsigned int i;
            for (i = 0; i < (unsigned int)size; i++) dst[i] = src[i];
        }
        jo_free(ptr);
        return np;
    }
}

struct _reent;

void *_malloc_r(struct _reent *r, size_t size)
{
    (void)r;
    return malloc(size);
}

void _free_r(struct _reent *r, void *ptr)
{
    (void)r;
    free(ptr);
}

void *_calloc_r(struct _reent *r, size_t nmemb, size_t size)
{
    (void)r;
    return calloc(nmemb, size);
}

void *_realloc_r(struct _reent *r, void *ptr, size_t size)
{
    (void)r;
    return realloc(ptr, size);
}

void *_sbrk(int incr)
{
    (void)incr;
    return (void *)-1;
}
