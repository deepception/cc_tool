"""A minimal terminal program for the control-mode tests (not a test itself).

cc-dash shows the control code only on a terminal whose master side is held by a
terminal program cc-setup recorded, and never records or accepts a generic
interpreter (python, a shell) as one. So tests that need "your terminal" use this
small compiled relay: it holds a pty master (forkpty), like a terminal emulator,
and relays it to its own stdin and stdout.

  relay ROWS COLS cmd args...     writes the command's pid on stderr, then relays

The parent closes every other fd it inherited (a terminal does not keep its
caller's pipes), the command keeps them (a --ready-fd pipe, say).
"""
import os
import shutil
import subprocess

RELAY_C = r"""
#include <errno.h>
#include <pty.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/select.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc < 4) return 2;
    struct winsize ws = {0};
    ws.ws_row = atoi(argv[1]); ws.ws_col = atoi(argv[2]);
    int master;
    pid_t pid = forkpty(&master, NULL, NULL, &ws);
    if (pid < 0) return 127;
    if (pid == 0) { execvp(argv[3], argv + 3); _exit(127); }
    for (int f = 3; f < 1024; f++) if (f != master) close(f);
    dprintf(2, "%d\n", (int)pid);
    char buf[65536];
    int in_open = 1;
    for (;;) {
        fd_set r; FD_ZERO(&r); FD_SET(master, &r); if (in_open) FD_SET(0, &r);
        if (select(master + 1, &r, 0, 0, 0) < 0) { if (errno == EINTR) continue; break; }
        if (FD_ISSET(master, &r)) {
            ssize_t n = read(master, buf, sizeof buf);
            if (n <= 0) break;
            for (ssize_t o = 0; o < n;) { ssize_t w = write(1, buf + o, n - o); if (w <= 0) break; o += w; }
        }
        if (in_open && FD_ISSET(0, &r)) {
            ssize_t n = read(0, buf, sizeof buf);
            if (n <= 0) in_open = 0; else if (write(master, buf, n) < 0) break;
        }
    }
    int st; waitpid(pid, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
"""


def build_relay(dest):
    """Compile RELAY_C to dest; dest, or None without a C compiler."""
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    src = dest + ".c"
    with open(src, "w") as fh:
        fh.write(RELAY_C)
    r = subprocess.run([cc, "-o", dest, src, "-lutil"], capture_output=True)
    return dest if r.returncode == 0 else None
