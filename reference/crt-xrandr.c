#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/extensions/Xrandr.h>

static int xerror = 0;

static int error_handler(Display *dpy, XErrorEvent *event)
{
    char message[256];
    XGetErrorText(dpy, event->error_code,
                  message, sizeof(message));
    fprintf(stderr, "X11 error: %s\n", message);
    xerror = 1;
    return 0;
}

int main(void)
{
    Display *dpy = XOpenDisplay(NULL);

    if (!dpy) {
        fprintf(stderr, "Cannot open X display\n");
        return 1;
    }

    XSetErrorHandler(error_handler);

    Window root = DefaultRootWindow(dpy);

    XRRScreenResources *res =
        XRRGetScreenResources(dpy, root);

    if (!res)
        return 1;

    RROutput output = None;

    for (int i = 0; i < res->noutput; i++) {
        XRROutputInfo *info =
            XRRGetOutputInfo(dpy, res, res->outputs[i]);

        if (info) {
            if (strcmp(info->name, "HDMI-1") == 0)
                output = res->outputs[i];

            XRRFreeOutputInfo(info);
        }

        if (output != None)
            break;
    }

    if (output == None) {
        fprintf(stderr, "HDMI-1 not found\n");
        return 1;
    }

    XRRModeInfo mode = {
        .width = 720,
        .height = 480,
        .dotClock = 13500000,

        .hSyncStart = 779,
        .hSyncEnd = 841,
        .hTotal = 858,
        .hSkew = 0,

        .vSyncStart = 488,
        .vSyncEnd = 494,
        .vTotal = 525,

        .name = "720x480i-crt",
        .nameLength = strlen("720x480i-crt"),

        .modeFlags = RR_HSyncNegative |
                     RR_VSyncNegative |
                     RR_Interlace |
                     RR_DoubleClock
    };

    RRMode id = XRRCreateMode(dpy, root, &mode);
    XSync(dpy, False);

    if (xerror || id == None)
        return 1;

    XRRAddOutputMode(dpy, output, id);
    XSync(dpy, False);

    if (xerror)
        return 1;

    printf("Custom mode registered: 0x%lx\n", id);
    printf("Mode flags: 0x%lx\n", mode.modeFlags);

    XRRFreeScreenResources(res);
    XCloseDisplay(dpy);

    return 0;
}
