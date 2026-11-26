/*
 * crt-shift - optional calibration tool; not installed.
 *
 * Shows a border/grid test pattern for a few seconds using the kernel's
 * standard 720x480i double-clocked mode with the horizontal sync moved by
 * SHIFT logical pixels, then restores the previous CRTC configuration. The
 * profile's +40 pixel shift was found this way.
 *
 * It needs DRM master, so the display manager must be stopped first:
 *   sudo systemctl stop lightdm
 *   sudo ./crt-shift --shift 40 --seconds 20
 *   sudo systemctl start lightdm
 * Run it from SSH, not from a session on the CRT itself.
 *
 * Build:
 *   gcc -Wall -Wextra -O2 crt-shift.c -o crt-shift $(pkg-config --cflags --libs libdrm)
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#define SHIFT_MIN (-10)
#define SHIFT_MAX 40

static void usage(FILE *out)
{
    fprintf(out,
        "usage: crt-shift [--device vc4] [--connector HDMI-A-1] [--shift N] [--seconds N]\n"
        "  --shift    horizontal sync shift in pixels, %d..%d (default 10)\n"
        "  --seconds  how long to show the pattern, 1..120 (default 20)\n"
        "Stop the display manager first; this tool needs DRM master.\n",
        SHIFT_MIN, SHIFT_MAX);
}

static int parse_int(const char *s, long lo, long hi, long *out)
{
    char *end = NULL;
    long v;
    if (!s || !*s)
        return -1;
    errno = 0;
    v = strtol(s, &end, 10);
    if (errno || *end || v < lo || v > hi)
        return -1;
    *out = v;
    return 0;
}

static int connector_name(drmModeConnector *c, char *buf, size_t len)
{
    const char *type = drmModeGetConnectorTypeName(c->connector_type);
    if (!type)
        return -1;
    snprintf(buf, len, "%s-%u", type, c->connector_type_id);
    return 0;
}

int main(int argc, char **argv)
{
    const char *device = "vc4";
    const char *want_connector = "HDMI-A-1";
    long shift = 10, seconds = 20;
    int fd = -1, rc = 1, i, found = 0, have_fb = 0, have_dumb = 0;
    drmModeRes *res = NULL;
    drmModeConnector *conn = NULL;
    drmModeCrtc *saved = NULL;
    drmModeModeInfo mode = { 0 };
    uint32_t crtc_id = 0, fb = 0, conn_id;
    struct drm_mode_create_dumb create = { .width = 0 };
    uint8_t *data = MAP_FAILED;

    for (i = 1; i < argc; i++) {
        const char *val = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(argv[i], "--help") || !strcmp(argv[i], "-h")) {
            usage(stdout);
            return 0;
        } else if (!strcmp(argv[i], "--device") && val) {
            device = val; i++;
        } else if (!strcmp(argv[i], "--connector") && val) {
            want_connector = val; i++;
        } else if (!strcmp(argv[i], "--shift") && val) {
            if (parse_int(val, SHIFT_MIN, SHIFT_MAX, &shift)) {
                fprintf(stderr, "Shift must be between %d and %d\n", SHIFT_MIN, SHIFT_MAX);
                return 2;
            }
            i++;
        } else if (!strcmp(argv[i], "--seconds") && val) {
            if (parse_int(val, 1, 120, &seconds)) {
                fprintf(stderr, "Seconds must be between 1 and 120\n");
                return 2;
            }
            i++;
        } else {
            usage(stderr);
            return 2;
        }
    }

    fd = drmOpen(device, NULL);
    if (fd < 0) {
        perror("drmOpen");
        return 1;
    }
    res = drmModeGetResources(fd);
    if (!res) {
        perror("drmModeGetResources");
        goto out;
    }

    for (i = 0; i < res->count_connectors && !conn; i++) {
        drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
        char name[64];
        if (!c)
            continue;
        if (connector_name(c, name, sizeof(name)) == 0 && !strcmp(name, want_connector))
            conn = c;
        else
            drmModeFreeConnector(c);
    }
    if (!conn) {
        fprintf(stderr, "Connector %s not found\n", want_connector);
        goto out;
    }
    if (conn->connection != DRM_MODE_CONNECTED)
        fprintf(stderr, "Warning: %s does not report a connected sink\n", want_connector);

    /* Prefer the CRTC currently driving the connector. */
    if (conn->encoder_id) {
        drmModeEncoder *enc = drmModeGetEncoder(fd, conn->encoder_id);
        if (enc) {
            crtc_id = enc->crtc_id;
            drmModeFreeEncoder(enc);
        }
    }
    for (i = 0; i < conn->count_encoders && !crtc_id; i++) {
        drmModeEncoder *enc = drmModeGetEncoder(fd, conn->encoders[i]);
        int j;
        if (!enc)
            continue;
        for (j = 0; j < res->count_crtcs; j++) {
            if (enc->possible_crtcs & (1u << j)) {
                crtc_id = res->crtcs[j];
                break;
            }
        }
        drmModeFreeEncoder(enc);
    }
    if (!crtc_id) {
        fprintf(stderr, "No CRTC available for %s\n", want_connector);
        goto out;
    }
    saved = drmModeGetCrtc(fd, crtc_id);

    for (i = 0; i < conn->count_modes; i++) {
        drmModeModeInfo m = conn->modes[i];
        if (m.hdisplay == 720 && m.vdisplay == 480 && m.clock == 13500 &&
            (m.flags & DRM_MODE_FLAG_INTERLACE) && (m.flags & DRM_MODE_FLAG_DBLCLK)) {
            mode = m;
            found = 1;
            break;
        }
    }
    if (!found) {
        fprintf(stderr, "480i mode not found\n");
        goto out;
    }
    if (mode.hsync_start + shift <= mode.hdisplay ||
        mode.hsync_end + shift > mode.htotal) {
        fprintf(stderr, "Shift %ld moves sync outside the blanking interval\n", shift);
        goto out;
    }
    mode.hsync_start += shift;
    mode.hsync_end += shift;

    create.width = 720;
    create.height = 480;
    create.bpp = 32;
    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create)) {
        perror("CREATE_DUMB");
        goto out;
    }
    have_dumb = 1;
    if (drmModeAddFB(fd, 720, 480, 24, 32, create.pitch, create.handle, &fb)) {
        perror("drmModeAddFB");
        goto out;
    }
    have_fb = 1;
    {
        struct drm_mode_map_dumb map = { .handle = create.handle };
        if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map)) {
            perror("MAP_DUMB");
            goto out;
        }
        data = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
        if (data == MAP_FAILED) {
            perror("mmap");
            goto out;
        }
    }
    for (int y = 0; y < 480; y++) {
        uint32_t *row = (uint32_t *)(data + (size_t)y * create.pitch);
        for (int x = 0; x < 720; x++) {
            int border = x < 4 || x >= 716 || y < 4 || y >= 476;
            int grid = x % 40 < 2 || y % 40 < 2;
            row[x] = border ? 0x00ffffff : grid ? 0x00aaaaaa : 0x00202020;
        }
    }

    conn_id = conn->connector_id;
    printf("Connector: %s (id %u), CRTC id %u\n", want_connector, conn_id, crtc_id);
    printf("Shift: %ld pixels\n", shift);
    printf("Sync start: %d\n", mode.hsync_start);
    printf("Sync end: %d\n", mode.hsync_end);
    printf("Flags: 0x%x\n", mode.flags);

    if (drmModeSetCrtc(fd, crtc_id, fb, 0, 0, &conn_id, 1, &mode)) {
        perror("drmModeSetCrtc (is the display manager still running?)");
        goto out;
    }
    printf("Test active for %ld seconds\n", seconds);
    fflush(stdout);
    sleep((unsigned int)seconds);
    rc = 0;

    if (saved && saved->mode_valid && saved->buffer_id) {
        if (drmModeSetCrtc(fd, crtc_id, saved->buffer_id, saved->x, saved->y, &conn_id, 1, &saved->mode))
            perror("restoring previous CRTC configuration");
    } else {
        drmModeSetCrtc(fd, crtc_id, 0, 0, 0, NULL, 0, NULL);
    }

out:
    if (data != MAP_FAILED)
        munmap(data, create.size);
    if (have_fb)
        drmModeRmFB(fd, fb);
    if (have_dumb) {
        struct drm_mode_destroy_dumb destroy = { .handle = create.handle };
        drmIoctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
    }
    if (saved)
        drmModeFreeCrtc(saved);
    if (conn)
        drmModeFreeConnector(conn);
    if (res)
        drmModeFreeResources(res);
    if (fd >= 0)
        drmClose(fd);
    return rc;
}
