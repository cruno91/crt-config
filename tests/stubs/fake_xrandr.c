/*
 * Test double for the subset of Xlib/XRandR used by crt-xrandr.c and the
 * archived reference/crt-xrandr.c. Compiled against the real X11 headers so
 * that struct layouts and flag constants are the genuine ones.
 *
 * State persists between invocations in the file named by FAKE_X_STATE,
 * emulating an X server that keeps user-created modes. Every mutating call
 * is appended to FAKE_X_LOG so tests can compare the exact requests made by
 * the archived and the new helper.
 *
 * State file lines:
 *   next <id>
 *   output <id> <name> <crtc-id|0>
 *   outmode <output-id> <mode-id>
 *   crtc <id> <mode-id|0>
 *   mode <id> <name> <dotclock> <w> <hss> <hse> <ht> <hskew> <h> <vss> <vse> <vt> <flags>
 *
 * FAKE_X_FAIL=<CallName> makes that call raise an X error.
 * FAKE_X_NODISPLAY=1 makes XOpenDisplay fail.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/extensions/Xrandr.h>

#define MAXN 64

struct fmode { unsigned long id; char name[64]; XRRModeInfo info; int live; };
struct foutput { unsigned long id; char name[32]; unsigned long crtc; unsigned long modes[MAXN]; int nmode; };
struct fcrtc { unsigned long id; unsigned long mode; };

static struct fmode modes[MAXN];
static int nmodes;
static struct foutput outputs[MAXN];
static int noutputs;
static struct fcrtc crtcs[MAXN];
static int ncrtcs;
static unsigned long next_id = 0x200;
static XErrorHandler handler;
static Display *the_dpy;

static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logf_(const char *fmt, ...)
{
    const char *path = getenv("FAKE_X_LOG");
    FILE *f;
    va_list ap;
    if (!path)
        return;
    f = fopen(path, "a");
    if (!f)
        return;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

static int should_fail(const char *call)
{
    const char *f = getenv("FAKE_X_FAIL");
    return f && strcmp(f, call) == 0;
}

static void raise_error(unsigned char code)
{
    XErrorEvent ev;
    memset(&ev, 0, sizeof(ev));
    ev.error_code = code;
    ev.request_code = 140;
    if (handler)
        handler(the_dpy, &ev);
}

static void load(void)
{
    const char *path = getenv("FAKE_X_STATE");
    char line[512];
    FILE *f;
    if (!path || !(f = fopen(path, "r"))) {
        fprintf(stderr, "fake_xrandr: FAKE_X_STATE missing\n");
        exit(99);
    }
    while (fgets(line, sizeof(line), f)) {
        char kind[16] = "";
        if (sscanf(line, "%15s", kind) != 1)
            continue;
        if (!strcmp(kind, "next")) {
            sscanf(line, "%*s %lx", &next_id);
        } else if (!strcmp(kind, "output")) {
            struct foutput *o = &outputs[noutputs++];
            sscanf(line, "%*s %lx %31s %lx", &o->id, o->name, &o->crtc);
        } else if (!strcmp(kind, "outmode")) {
            unsigned long oid, mid;
            int i;
            sscanf(line, "%*s %lx %lx", &oid, &mid);
            for (i = 0; i < noutputs; i++)
                if (outputs[i].id == oid)
                    outputs[i].modes[outputs[i].nmode++] = mid;
        } else if (!strcmp(kind, "crtc")) {
            struct fcrtc *c = &crtcs[ncrtcs++];
            sscanf(line, "%*s %lx %lx", &c->id, &c->mode);
        } else if (!strcmp(kind, "mode")) {
            struct fmode *m = &modes[nmodes++];
            unsigned long flags;
            sscanf(line, "%*s %lx %63s %lu %u %u %u %u %u %u %u %u %u %lx",
                   &m->id, m->name, &m->info.dotClock,
                   &m->info.width, &m->info.hSyncStart, &m->info.hSyncEnd, &m->info.hTotal, &m->info.hSkew,
                   &m->info.height, &m->info.vSyncStart, &m->info.vSyncEnd, &m->info.vTotal, &flags);
            m->info.modeFlags = flags;
            m->info.id = m->id;
            m->live = 1;
        }
    }
    fclose(f);
}

static void save(void)
{
    const char *path = getenv("FAKE_X_STATE");
    FILE *f = fopen(path, "w");
    int i, j;
    if (!f)
        exit(98);
    fprintf(f, "next %lx\n", next_id);
    for (i = 0; i < noutputs; i++) {
        fprintf(f, "output %lx %s %lx\n", outputs[i].id, outputs[i].name, outputs[i].crtc);
        for (j = 0; j < outputs[i].nmode; j++)
            fprintf(f, "outmode %lx %lx\n", outputs[i].id, outputs[i].modes[j]);
    }
    for (i = 0; i < ncrtcs; i++)
        fprintf(f, "crtc %lx %lx\n", crtcs[i].id, crtcs[i].mode);
    for (i = 0; i < nmodes; i++) {
        XRRModeInfo *m = &modes[i].info;
        if (!modes[i].live)
            continue;
        fprintf(f, "mode %lx %s %lu %u %u %u %u %u %u %u %u %u %lx\n",
                modes[i].id, modes[i].name, m->dotClock,
                m->width, m->hSyncStart, m->hSyncEnd, m->hTotal, m->hSkew,
                m->height, m->vSyncStart, m->vSyncEnd, m->vTotal, (unsigned long)m->modeFlags);
    }
    fclose(f);
}

Display *XOpenDisplay(const char *name)
{
    _XPrivDisplay d;
    (void)name;
    if (getenv("FAKE_X_NODISPLAY"))
        return NULL;
    load();
    d = calloc(1, sizeof(*d));
    d->screens = calloc(1, sizeof(Screen));
    d->screens[0].root = 1;
    d->default_screen = 0;
    d->nscreens = 1;
    the_dpy = (Display *)d;
    return the_dpy;
}

int XCloseDisplay(Display *dpy)
{
    _XPrivDisplay d = (_XPrivDisplay)dpy;
    save();
    free(d->screens);
    free(d);
    return 0;
}

XErrorHandler XSetErrorHandler(XErrorHandler h)
{
    XErrorHandler old = handler;
    handler = h;
    return old;
}

int XGetErrorText(Display *dpy, int code, char *buf, int len)
{
    (void)dpy;
    snprintf(buf, (size_t)len, "FakeError(%d)", code);
    return 0;
}

int XSync(Display *dpy, Bool discard)
{
    (void)dpy;
    (void)discard;
    return 0;
}

Bool XRRQueryExtension(Display *dpy, int *ev, int *err)
{
    (void)dpy;
    *ev = 89;
    *err = 147;
    return True;
}

Status XRRQueryVersion(Display *dpy, int *major, int *minor)
{
    (void)dpy;
    *major = 1;
    *minor = 6;
    return 1;
}

static XRRScreenResources *resources(void)
{
    XRRScreenResources *r = calloc(1, sizeof(*r));
    int i, n = 0;
    r->noutput = noutputs;
    r->outputs = calloc(MAXN, sizeof(RROutput));
    for (i = 0; i < noutputs; i++)
        r->outputs[i] = outputs[i].id;
    r->ncrtc = ncrtcs;
    r->crtcs = calloc(MAXN, sizeof(RRCrtc));
    for (i = 0; i < ncrtcs; i++)
        r->crtcs[i] = crtcs[i].id;
    r->modes = calloc(MAXN, sizeof(XRRModeInfo));
    for (i = 0; i < nmodes; i++) {
        if (!modes[i].live)
            continue;
        r->modes[n] = modes[i].info;
        r->modes[n].id = modes[i].id;
        r->modes[n].name = modes[i].name;
        r->modes[n].nameLength = (unsigned int)strlen(modes[i].name);
        n++;
    }
    r->nmode = n;
    return r;
}

XRRScreenResources *XRRGetScreenResources(Display *dpy, Window w)
{
    (void)dpy; (void)w;
    logf_("GetScreenResources");
    return resources();
}

XRRScreenResources *XRRGetScreenResourcesCurrent(Display *dpy, Window w)
{
    (void)dpy; (void)w;
    logf_("GetScreenResourcesCurrent");
    return resources();
}

void XRRFreeScreenResources(XRRScreenResources *r)
{
    free(r->outputs);
    free(r->crtcs);
    free(r->modes);
    free(r);
}

XRROutputInfo *XRRGetOutputInfo(Display *dpy, XRRScreenResources *res, RROutput id)
{
    int i, j;
    (void)dpy; (void)res;
    for (i = 0; i < noutputs; i++) {
        if (outputs[i].id == id) {
            XRROutputInfo *o = calloc(1, sizeof(*o));
            o->name = strdup(outputs[i].name);
            o->nameLen = (int)strlen(o->name);
            o->crtc = outputs[i].crtc;
            o->connection = RR_Connected;
            o->nmode = outputs[i].nmode;
            o->modes = calloc(MAXN, sizeof(RRMode));
            for (j = 0; j < outputs[i].nmode; j++)
                o->modes[j] = outputs[i].modes[j];
            return o;
        }
    }
    return NULL;
}

void XRRFreeOutputInfo(XRROutputInfo *o)
{
    free(o->name);
    free(o->modes);
    free(o);
}

XRRCrtcInfo *XRRGetCrtcInfo(Display *dpy, XRRScreenResources *res, RRCrtc id)
{
    int i;
    (void)dpy; (void)res;
    for (i = 0; i < ncrtcs; i++) {
        if (crtcs[i].id == id) {
            XRRCrtcInfo *c = calloc(1, sizeof(*c));
            c->mode = crtcs[i].mode;
            return c;
        }
    }
    return NULL;
}

void XRRFreeCrtcInfo(XRRCrtcInfo *c)
{
    free(c);
}

RRMode XRRCreateMode(Display *dpy, Window w, XRRModeInfo *m)
{
    struct fmode *f;
    (void)dpy; (void)w;
    logf_("CreateMode name=%.*s dotClock=%lu h=%u,%u,%u,%u hskew=%u v=%u,%u,%u,%u flags=0x%lx",
          (int)m->nameLength, m->name, m->dotClock,
          m->width, m->hSyncStart, m->hSyncEnd, m->hTotal, m->hSkew,
          m->height, m->vSyncStart, m->vSyncEnd, m->vTotal, (unsigned long)m->modeFlags);
    if (should_fail("CreateMode")) {
        raise_error(BadMatch);
        return None;
    }
    f = &modes[nmodes++];
    f->id = next_id++;
    snprintf(f->name, sizeof(f->name), "%.*s", (int)m->nameLength, m->name);
    f->info = *m;
    f->info.id = f->id;
    f->live = 1;
    save();
    return f->id;
}

void XRRAddOutputMode(Display *dpy, RROutput output, RRMode mode)
{
    int i;
    (void)dpy;
    logf_("AddOutputMode output=0x%lx mode=0x%lx", (unsigned long)output, (unsigned long)mode);
    if (should_fail("AddOutputMode")) {
        raise_error(BadMatch);
        return;
    }
    for (i = 0; i < noutputs; i++)
        if (outputs[i].id == output)
            outputs[i].modes[outputs[i].nmode++] = mode;
    save();
}

void XRRDeleteOutputMode(Display *dpy, RROutput output, RRMode mode)
{
    int i, j, k;
    (void)dpy;
    logf_("DeleteOutputMode output=0x%lx mode=0x%lx", (unsigned long)output, (unsigned long)mode);
    for (i = 0; i < noutputs; i++) {
        if (outputs[i].id != output)
            continue;
        for (j = k = 0; j < outputs[i].nmode; j++)
            if (outputs[i].modes[j] != mode)
                outputs[i].modes[k++] = outputs[i].modes[j];
        outputs[i].nmode = k;
    }
    save();
}

void XRRDestroyMode(Display *dpy, RRMode mode)
{
    int i;
    (void)dpy;
    logf_("DestroyMode mode=0x%lx", (unsigned long)mode);
    for (i = 0; i < nmodes; i++)
        if (modes[i].id == mode)
            modes[i].live = 0;
    save();
}
