"""Zyvega CLI — control Zhiyun Vega PL103 lights via Bluetooth Mesh.

Uses GLib main loop with non-blocking stdin for interactive commands
while D-Bus callbacks fire in the background.
"""

import fcntl
import os
import sys

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from zyvega import MeshController

HELP_TEXT = """\
Zyvega - Zhiyun Light Controller

Commands:
  scan [secs]       Scan for unprovisioned devices (default: 10s)
  provision <idx>   Provision device by scan index
  configure <addr>  Get composition data, add app key, bind model
  nodes             List provisioned nodes
  reset             Delete network and start fresh
  quit              Exit
"""


def handle_command(controller, line):
    """Parse and dispatch a CLI command."""
    parts = line.strip().split()
    if not parts:
        return True

    cmd = parts[0].lower()

    if cmd in ("quit", "exit", "q"):
        return False

    elif cmd == "help":
        print(HELP_TEXT)

    elif cmd == "scan":
        seconds = 10
        if len(parts) > 1:
            try:
                seconds = int(parts[1])
            except ValueError:
                print("Usage: scan [seconds]")
                return True
        controller.start_scan(seconds)

    elif cmd == "provision":
        if len(parts) < 2:
            print("Usage: provision <scan_index>")
            return True
        try:
            idx = int(parts[1])
        except ValueError:
            print("Usage: provision <scan_index>")
            return True
        controller.provision_device(idx)

    elif cmd == "configure":
        if len(parts) < 2:
            print("Usage: configure <unicast_addr>")
            return True
        try:
            addr = int(parts[1], 0)  # supports 0x prefix
        except ValueError:
            print("Usage: configure <unicast_addr>  (e.g. 0x0002)")
            return True
        controller.configure_device(addr)

    elif cmd == "nodes":
        controller.list_nodes()

    elif cmd == "reset":
        controller.reset_network()

    else:
        print(f"Unknown command: {cmd}. Type 'help' for available commands.")

    return True


def on_stdin(fd, condition, controller):
    """GLib IO callback for stdin."""
    if condition & GLib.IO_IN:
        try:
            line = sys.stdin.readline()
        except (IOError, OSError):
            return True

        if not line:
            # EOF
            loop.quit()
            return False

        if not handle_command(controller, line):
            loop.quit()
            return False

        sys.stdout.write("> ")
        sys.stdout.flush()

    return True


loop = None


def main():
    global loop

    # Initialize D-Bus with GLib main loop integration
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # Create our mesh controller (registers D-Bus objects)
    controller = MeshController(bus)

    # Set stdin to non-blocking
    flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Print banner
    print(HELP_TEXT)

    # Set up stdin watch
    GLib.io_add_watch(sys.stdin, GLib.IO_IN, on_stdin, controller)

    # Defer initialize() so the main loop is running when the daemon
    # calls back to our GetManagedObjects during CreateNetwork/Attach
    def _deferred_init():
        controller.initialize()
        sys.stdout.write("> ")
        sys.stdout.flush()
        return False  # don't repeat

    GLib.idle_add(_deferred_init)

    # Run main loop
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        # Restore stdin flags
        fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags)


if __name__ == "__main__":
    main()
