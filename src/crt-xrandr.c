/*
 * crt-xrandr - register a custom XRandR mode whose flags the xrandr(1)
 * command line cannot express (notably RR_DoubleClock, i.e. HDMI pixel
 * repetition) and attach it to an output.
 *
 * Run without arguments it creates the sony-trinitron-lontium mode:
 *
 *   720x480i-crt  13.500 MHz  H 720 779 841 858  V 480 488 494 525
 *   -HSync -VSync Interlace DoubleClock (modeFlags 0x101a) on HDMI-1
 *
 * An existing mode with the same name is reused if identical, reported as a
 * conflict (exit 3) if not, and replaced only with --replace. --query is
 * read-only and avoids an output re-probe (XRRGetScreenResourcesCurrent).
 *
 * It does not switch the output to the mode; display-setup does that with
 * xrandr(1) so that margins are applied in the same request.
 *
 * Build:
 *   gcc -Wall -Wextra -O2 crt-xrandr.c -o crt-xrandr $(pkg-config --cflags --libs xrandr x11)
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/extensions/Xrandr.h>

enum {
    EXIT_OK = 0,
    EXIT_X11 = 1,
    EXIT_USAGE = 2,
    EXIT_CONFLICT = 3,
    EXIT_NO_OUTPUT = 4,
    EXIT_NOT_ACTIVE = 5,
    EXIT_ABSENT = 6,
};

#define MAX_CONFLICTS 32

static int xerror = 0;

static int error_handler(Display *dpy, XErrorEvent *event)
{
    char message[256];
    XGetErrorText(dpy, event->error_code, message, sizeof(message));
    fprintf(stderr, "crt-xrandr: X11 error: %s (request %u.%u)\n",
            message, event->request_code, event->minor_code);
    xerror = 1;
    return 0;
}

struct flag_name {
    const char *name;
    unsigned long bit;
};

static const struct flag_name flag_names[] = {
    { "phsync", RR_HSyncPositive },
    { "nhsync", RR_HSyncNegative },
    { "pvsync", RR_VSyncPositive },
    { "nvsync", RR_VSyncNegative },
    { "interlace", RR_Interlace },
    { "dblscan", RR_DoubleScan },
    { "csync", RR_CSync },
    { "pcsync", RR_CSyncPositive },
    { "ncsync", RR_CSyncNegative },
    { "dblclk", RR_DoubleClock },
};

struct options {
    const char *output;
    const char *name;
    unsigned long clock_khz;
    unsigned int h[4];
    unsigned int v[4];
    unsigned long flags;
    int replace;
    int query;
    int require_active;
    int print;
};

static void usage(FILE *out)
{
    fprintf(out,
        "usage: crt-xrandr [--output NAME] [--name MODE] [--clock KHZ]\n"
        "                  [--h ACTIVE,SYNC_START,SYNC_END,TOTAL]\n"
        "                  [--v ACTIVE,SYNC_START,SYNC_END,TOTAL]\n"
        "                  [--flags nhsync,nvsync,interlace,dblclk,...]\n"
        "                  [--replace] [--query [--require-active]] [--print]\n"
        "\n"
        "Defaults: HDMI-1, 720x480i-crt, 13500 kHz,\n"
        "H 720,779,841,858, V 480,488,494,525, flags nhsync,nvsync,interlace,dblclk.\n"
        "\n"
        "exit codes: 0 ok, 1 X11 error, 2 usage, 3 conflicting mode,\n"
        "            4 output not found, 5 mode not attached/active (--query),\n"
        "            6 mode absent (--query)\n");
}

static int parse_ulong(const char *s, unsigned long lo, unsigned long hi,
                       unsigned long *out)
{
    char *end = NULL;
    unsigned long v;

    if (!s || !*s || *s == '-' || *s == '+')
        return -1;
    errno = 0;
    v = strtoul(s, &end, 10);
    if (errno || !end || *end || v < lo || v > hi)
        return -1;
    *out = v;
    return 0;
}

static int parse_quad(const char *s, unsigned int q[4])
{
    char buf[64];
    char *save = NULL;
    char *tok;
    int n = 0;

    if (strlen(s) >= sizeof(buf))
        return -1;
    /* strtok_r collapses empty fields; reject them explicitly. */
    if (strstr(s, ",,") || s[0] == ',' || s[strlen(s) - 1] == ',')
        return -1;
    strcpy(buf, s);
    for (tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        unsigned long v;
        if (n >= 4 || parse_ulong(tok, 1, 65535, &v))
            return -1;
        q[n++] = (unsigned int)v;
    }
    if (n != 4)
        return -1;
    if (!(q[0] <= q[1] && q[1] < q[2] && q[2] <= q[3]))
        return -1;
    return 0;
}

static int parse_flags(const char *s, unsigned long *out)
{
    char buf[256];
    char *save = NULL;
    char *tok;
    unsigned long flags = 0;

    if (strlen(s) >= sizeof(buf) || !*s)
        return -1;
    strcpy(buf, s);
    for (tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        size_t i;
        int found = 0;
        for (i = 0; i < sizeof(flag_names) / sizeof(flag_names[0]); i++) {
            if (strcmp(tok, flag_names[i].name) == 0) {
                flags |= flag_names[i].bit;
                found = 1;
                break;
            }
        }
        if (!found)
            return -1;
    }
    if ((flags & RR_HSyncPositive) && (flags & RR_HSyncNegative))
        return -1;
    if ((flags & RR_VSyncPositive) && (flags & RR_VSyncNegative))
        return -1;
    *out = flags;
    return 0;
}

static void build_mode(const struct options *o, XRRModeInfo *m)
{
    memset(m, 0, sizeof(*m));
    m->width = o->h[0];
    m->hSyncStart = o->h[1];
    m->hSyncEnd = o->h[2];
    m->hTotal = o->h[3];
    m->hSkew = 0;
    m->height = o->v[0];
    m->vSyncStart = o->v[1];
    m->vSyncEnd = o->v[2];
    m->vTotal = o->v[3];
    m->dotClock = o->clock_khz * 1000UL;
    m->name = (char *)o->name;
    m->nameLength = (unsigned int)strlen(o->name);
    m->modeFlags = o->flags;
}

static void print_mode(const char *label, const XRRModeInfo *m)
{
    printf("%sname=%.*s clock_khz=%lu h=%u,%u,%u,%u v=%u,%u,%u,%u hskew=%u flags=0x%lx\n",
           label, (int)m->nameLength, m->name, m->dotClock / 1000UL,
           m->width, m->hSyncStart, m->hSyncEnd, m->hTotal,
           m->height, m->vSyncStart, m->vSyncEnd, m->vTotal,
           m->hSkew, (unsigned long)m->modeFlags);
}

static int same_name(const XRRModeInfo *m, const char *name)
{
    size_t len = strlen(name);
    return m->nameLength == len && m->name && memcmp(m->name, name, len) == 0;
}

static int same_timing(const XRRModeInfo *a, const XRRModeInfo *b)
{
    return a->width == b->width && a->height == b->height &&
           a->dotClock == b->dotClock &&
           a->hSyncStart == b->hSyncStart && a->hSyncEnd == b->hSyncEnd &&
           a->hTotal == b->hTotal && a->hSkew == b->hSkew &&
           a->vSyncStart == b->vSyncStart && a->vSyncEnd == b->vSyncEnd &&
           a->vTotal == b->vTotal && a->modeFlags == b->modeFlags;
}

static int output_has_mode(const XRROutputInfo *info, RRMode id)
{
    int i;
    for (i = 0; i < info->nmode; i++)
        if (info->modes[i] == id)
            return 1;
    return 0;
}

static int mode_in_use(Display *dpy, XRRScreenResources *res, RRMode id)
{
    int i, used = 0;
    for (i = 0; i < res->ncrtc && !used; i++) {
        XRRCrtcInfo *crtc = XRRGetCrtcInfo(dpy, res, res->crtcs[i]);
        if (crtc) {
            used = crtc->mode == id;
            XRRFreeCrtcInfo(crtc);
        }
    }
    return used;
}

static int parse_args(int argc, char **argv, struct options *o)
{
    int i;
    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *val = (i + 1 < argc) ? argv[i + 1] : NULL;
        unsigned long tmp;

        if (strcmp(a, "--help") == 0 || strcmp(a, "-h") == 0) {
            usage(stdout);
            exit(EXIT_OK);
        } else if (strcmp(a, "--replace") == 0) {
            o->replace = 1;
        } else if (strcmp(a, "--query") == 0) {
            o->query = 1;
        } else if (strcmp(a, "--require-active") == 0) {
            o->require_active = 1;
        } else if (strcmp(a, "--print") == 0) {
            o->print = 1;
        } else if (!val) {
            fprintf(stderr, "crt-xrandr: missing value for %s\n", a);
            return -1;
        } else if (strcmp(a, "--output") == 0) {
            if (!*val || strlen(val) > 64) return -1;
            o->output = val; i++;
        } else if (strcmp(a, "--name") == 0) {
            if (!*val || strlen(val) > 64) return -1;
            o->name = val; i++;
        } else if (strcmp(a, "--clock") == 0) {
            if (parse_ulong(val, 1000, 600000, &tmp)) {
                fprintf(stderr, "crt-xrandr: invalid --clock %s (kHz)\n", val);
                return -1;
            }
            o->clock_khz = tmp; i++;
        } else if (strcmp(a, "--h") == 0) {
            if (parse_quad(val, o->h)) {
                fprintf(stderr, "crt-xrandr: invalid --h %s\n", val);
                return -1;
            }
            i++;
        } else if (strcmp(a, "--v") == 0) {
            if (parse_quad(val, o->v)) {
                fprintf(stderr, "crt-xrandr: invalid --v %s\n", val);
                return -1;
            }
            i++;
        } else if (strcmp(a, "--flags") == 0) {
            if (parse_flags(val, &o->flags)) {
                fprintf(stderr, "crt-xrandr: invalid --flags %s\n", val);
                return -1;
            }
            i++;
        } else {
            fprintf(stderr, "crt-xrandr: unknown argument %s\n", a);
            return -1;
        }
    }
    if (o->replace && o->query) {
        fprintf(stderr, "crt-xrandr: --replace and --query are mutually exclusive\n");
        return -1;
    }
    if (o->require_active && !o->query) {
        fprintf(stderr, "crt-xrandr: --require-active needs --query\n");
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct options o = {
        .output = "HDMI-1",
        .name = "720x480i-crt",
        .clock_khz = 13500,
        .h = { 720, 779, 841, 858 },
        .v = { 480, 488, 494, 525 },
        .flags = RR_HSyncNegative | RR_VSyncNegative | RR_Interlace | RR_DoubleClock,
    };
    XRRModeInfo want;
    Display *dpy = NULL;
    XRRScreenResources *res = NULL;
    XRROutputInfo *oinfo = NULL;
    RROutput output = None;
    RRMode match = None;
    RRMode conflicts[MAX_CONFLICTS];
    int nconflict = 0, noutput_match = 0;
    int rc = EXIT_X11;
    int i, ev_base, err_base, major = 0, minor = 0;
    Window root;

    if (parse_args(argc, argv, &o)) {
        usage(stderr);
        return EXIT_USAGE;
    }
    build_mode(&o, &want);

    if (o.print) {
        print_mode("", &want);
        return EXIT_OK;
    }

    dpy = XOpenDisplay(NULL);
    if (!dpy) {
        const char *d = getenv("DISPLAY");
        fprintf(stderr, "crt-xrandr: cannot open X display '%s'\n", d ? d : "(unset)");
        return EXIT_X11;
    }
    XSetErrorHandler(error_handler);

    if (!XRRQueryExtension(dpy, &ev_base, &err_base) ||
        !XRRQueryVersion(dpy, &major, &minor) ||
        major < 1 || (major == 1 && minor < 2)) {
        fprintf(stderr, "crt-xrandr: RandR >= 1.2 is required\n");
        goto out;
    }

    root = DefaultRootWindow(dpy);
    res = o.query ? XRRGetScreenResourcesCurrent(dpy, root)
                  : XRRGetScreenResources(dpy, root);
    if (!res) {
        fprintf(stderr, "crt-xrandr: cannot read screen resources\n");
        goto out;
    }

    for (i = 0; i < res->noutput; i++) {
        XRROutputInfo *info = XRRGetOutputInfo(dpy, res, res->outputs[i]);
        if (!info)
            continue;
        if (strcmp(info->name, o.output) == 0) {
            noutput_match++;
            if (!oinfo) {
                output = res->outputs[i];
                oinfo = info;
                continue;
            }
        }
        XRRFreeOutputInfo(info);
    }
    if (noutput_match != 1) {
        fprintf(stderr, "crt-xrandr: output %s %s\n", o.output,
                noutput_match ? "is ambiguous" : "not found");
        rc = EXIT_NO_OUTPUT;
        goto out;
    }

    for (i = 0; i < res->nmode; i++) {
        XRRModeInfo *m = &res->modes[i];
        if (!same_name(m, o.name))
            continue;
        if (same_timing(m, &want)) {
            if (match == None)
                match = m->id;
        } else {
            if (nconflict < MAX_CONFLICTS)
                conflicts[nconflict] = m->id;
            nconflict++;
            print_mode("crt-xrandr: existing conflicting mode: ", m);
        }
    }
    if (nconflict > MAX_CONFLICTS) {
        fprintf(stderr, "crt-xrandr: too many conflicting modes\n");
        rc = EXIT_CONFLICT;
        goto out;
    }

    if (o.query) {
        const char *state;
        if (match == None) {
            state = nconflict ? "conflict" : "absent";
            rc = nconflict ? EXIT_CONFLICT : EXIT_ABSENT;
        } else if (!output_has_mode(oinfo, match)) {
            state = "detached";
            rc = EXIT_NOT_ACTIVE;
        } else {
            int active = 0;
            if (oinfo->crtc != None) {
                XRRCrtcInfo *crtc = XRRGetCrtcInfo(dpy, res, oinfo->crtc);
                if (crtc) {
                    active = crtc->mode == match;
                    XRRFreeCrtcInfo(crtc);
                }
            }
            state = active ? "active" : "attached";
            rc = (active || !o.require_active) ? EXIT_OK : EXIT_NOT_ACTIVE;
        }
        printf("state=%s output=%s mode=0x%lx ", state, o.output, (unsigned long)match);
        print_mode("", &want);
        goto out;
    }

    if (nconflict) {
        if (!o.replace) {
            fprintf(stderr,
                    "crt-xrandr: a mode named %s already exists with different timings or flags; "
                    "rerun with --replace to remove it\n", o.name);
            rc = EXIT_CONFLICT;
            goto out;
        }
        for (i = 0; i < nconflict; i++) {
            int j;
            if (mode_in_use(dpy, res, conflicts[i])) {
                fprintf(stderr,
                        "crt-xrandr: conflicting mode 0x%lx is in use; switch the output to another "
                        "mode first\n", (unsigned long)conflicts[i]);
                rc = EXIT_CONFLICT;
                goto out;
            }
            for (j = 0; j < res->noutput; j++) {
                XRROutputInfo *info = XRRGetOutputInfo(dpy, res, res->outputs[j]);
                if (info) {
                    if (output_has_mode(info, conflicts[i]))
                        XRRDeleteOutputMode(dpy, res->outputs[j], conflicts[i]);
                    XRRFreeOutputInfo(info);
                }
            }
            XRRDestroyMode(dpy, conflicts[i]);
            XSync(dpy, False);
            if (xerror)
                goto out;
            printf("Removed conflicting mode: 0x%lx\n", (unsigned long)conflicts[i]);
        }
    }

    if (match == None) {
        match = XRRCreateMode(dpy, root, &want);
        XSync(dpy, False);
        if (xerror || match == None)
            goto out;
        printf("Custom mode registered: 0x%lx\n", (unsigned long)match);
    } else {
        printf("Custom mode already present: 0x%lx\n", (unsigned long)match);
    }

    if (!output_has_mode(oinfo, match)) {
        XRRAddOutputMode(dpy, output, match);
        XSync(dpy, False);
        if (xerror)
            goto out;
        printf("Mode attached to %s\n", o.output);
    }

    printf("Mode flags: 0x%lx\n", (unsigned long)want.modeFlags);
    rc = EXIT_OK;

out:
    if (oinfo)
        XRRFreeOutputInfo(oinfo);
    if (res)
        XRRFreeScreenResources(res);
    if (dpy)
        XCloseDisplay(dpy);
    return rc;
}
