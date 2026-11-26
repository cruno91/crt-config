#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <string.h>
#include <sys/mman.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

int main(int argc, char **argv)
{
    int shift = argc > 1 ? atoi(argv[1]) : 10;

    if (shift < -10 || shift > 40) {
        fprintf(stderr, "Shift must be between -10 and 30\n");
        return 1;
    }

    int fd = drmOpen("vc4", NULL);
    if (fd < 0) {
        perror("drmOpen");
        return 1;
    }

    drmModeConnector *conn = drmModeGetConnector(fd, 35);
    if (!conn) {
        fprintf(stderr, "Connector unavailable\n");
        return 1;
    }

    drmModeModeInfo mode;
    int found = 0;

    for (int i = 0; i < conn->count_modes; i++) {
        drmModeModeInfo m = conn->modes[i];

        if (m.hdisplay == 720 &&
            m.vdisplay == 480 &&
            m.clock == 13500 &&
            (m.flags & DRM_MODE_FLAG_INTERLACE) &&
            (m.flags & DRM_MODE_FLAG_DBLCLK)) {
            mode = m;
            found = 1;
            break;
        }
    }

    if (!found) {
        fprintf(stderr, "480i mode not found\n");
        return 1;
    }

    mode.hsync_start += shift;
    mode.hsync_end += shift;

    struct drm_mode_create_dumb create = {
        .width = 720,
        .height = 480,
        .bpp = 32
    };

    if (drmIoctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create)) {
        perror("CREATE_DUMB");
        return 1;
    }

    uint32_t fb;

    if (drmModeAddFB(fd, 720, 480, 24, 32,
                     create.pitch, create.handle, &fb)) {
        perror("drmModeAddFB");
        return 1;
    }

    struct drm_mode_map_dumb map = {
        .handle = create.handle
    };

    if (drmIoctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map)) {
        perror("MAP_DUMB");
        return 1;
    }

    uint8_t *data = mmap(NULL, create.size,
                         PROT_READ | PROT_WRITE,
                         MAP_SHARED, fd, map.offset);

    if (data == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    for (int y = 0; y < 480; y++) {
        uint32_t *row = (uint32_t *)(data + y * create.pitch);

        for (int x = 0; x < 720; x++) {
            int border = x < 4 || x >= 716 ||
                         y < 4 || y >= 476;

            int grid = x % 40 < 2 || y % 40 < 2;

            row[x] = border ? 0x00ffffff :
                       grid ? 0x00aaaaaa :
                              0x00202020;
        }
    }

    uint32_t connector = 35;

    printf("Shift: %d pixels\n", shift);
    printf("Sync start: %d\n", mode.hsync_start);
    printf("Sync end: %d\n", mode.hsync_end);
    printf("Flags: 0x%x\n", mode.flags);

    if (drmModeSetCrtc(fd, 102, fb, 0, 0,
                       &connector, 1, &mode)) {
        perror("drmModeSetCrtc");
        return 1;
    }

    printf("Test active for 20 seconds\n");
    sleep(20);

    drmModeSetCrtc(fd, 102, 0, 0, 0, NULL, 0, NULL);
    drmModeRmFB(fd, fb);
    munmap(data, create.size);

    drmModeFreeConnector(conn);
    drmClose(fd);

    return 0;
}
