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

from gatt import GattController

HELP_TEXT = """\
Zyvega - Zhiyun Light Controller

BLE commands (direct GATT control):
  ble scan [secs]       Scan for Zhiyun lights (0xFEE9 service)
  ble connect <idx|mac> Connect to a light
  ble disconnect        Disconnect
  ble status            Show connection state
  ble raw <hex>         Send raw bytes (no framing)
  ble send <cid> [hex]  Send framed ZYBL command (e.g. ble send 2003)
  ble info              Query device info (CID 0x2003)

Mesh commands (requires bluetooth-mesh.service):
  mesh start            Connect to mesh daemon
  mesh scan [secs]      Scan for unprovisioned devices
  mesh provision <idx>  Provision device by scan index
  mesh configure <addr> Get composition data, add app key, bind
  mesh nodes            List provisioned nodes
  mesh reset            Delete network and start fresh

  quit                  Exit
"""


def handle_ble_command(gatt, parts):
    """Handle 'ble <subcommand>' commands."""
    if len(parts) < 2:
        print("Usage: ble <scan|connect|disconnect|status|raw|send|info> ...")
        return

    sub = parts[1].lower()

    if sub == "scan":
        seconds = 10
        if len(parts) > 2:
            try:
                seconds = int(parts[2])
            except ValueError:
                print("Usage: ble scan [seconds]")
                return
        gatt.scan(seconds)

    elif sub == "connect":
        if len(parts) < 3:
            print("Usage: ble connect <index|MAC>")
            return
        target = parts[2]
        if ":" in target:
            gatt.connect(target)
        else:
            try:
                gatt.connect(int(target))
            except ValueError:
                print("Usage: ble connect <index|MAC>")

    elif sub == "disconnect":
        gatt.disconnect()

    elif sub == "status":
        gatt.status()

    elif sub == "raw":
        if len(parts) < 3:
            print("Usage: ble raw <hex bytes>")
            return
        hex_str = "".join(parts[2:]).replace("-", "").replace(":", "")
        try:
            data = bytes.fromhex(hex_str)
        except ValueError:
            print("Invalid hex string.")
            return
        gatt.write_raw(data)

    elif sub == "send":
        if len(parts) < 3:
            print("Usage: ble send <cid_hex> [payload_hex]")
            return
        try:
            cid = int(parts[2], 16)
        except ValueError:
            print("CID must be hex (e.g. 1001, 2003).")
            return
        payload = b""
        if len(parts) > 3:
            hex_str = "".join(parts[3:]).replace("-", "").replace(":", "")
            try:
                payload = bytes.fromhex(hex_str)
            except ValueError:
                print("Invalid payload hex.")
                return
        gatt.send_command(cid, payload)

    elif sub == "info":
        gatt.send_command(0x2003)

    else:
        print(f"Unknown ble subcommand: {sub}")


def handle_mesh_command(state, parts):
    """Handle 'mesh <subcommand>' commands. Lazy-inits the mesh controller."""
    if len(parts) < 2:
        print("Usage: mesh <start|scan|provision|configure|nodes|reset> ...")
        return

    sub = parts[1].lower()

    if sub == "start":
        if state["mesh"]:
            print("[mesh] Already started.")
            return
        try:
            from zyvega import MeshController
            state["mesh"] = MeshController(state["bus"])
            state["mesh"].initialize()
        except dbus.exceptions.DBusException as e:
            print(f"[mesh] Cannot connect to bluetooth-mesh daemon: {e.get_dbus_message()}")
            print("[mesh] Is bluetooth-mesh.service running?")
            state["mesh"] = None
        return

    controller = state["mesh"]
    if not controller:
        print("[mesh] Not started. Run 'mesh start' first (requires bluetooth-mesh.service).")
        return

    if sub == "scan":
        seconds = 10
        if len(parts) > 2:
            try:
                seconds = int(parts[2])
            except ValueError:
                print("Usage: mesh scan [seconds]")
                return
        controller.start_scan(seconds)

    elif sub == "provision":
        if len(parts) < 3:
            print("Usage: mesh provision <scan_index>")
            return
        try:
            idx = int(parts[2])
        except ValueError:
            print("Usage: mesh provision <scan_index>")
            return
        controller.provision_device(idx)

    elif sub == "configure":
        if len(parts) < 3:
            print("Usage: mesh configure <unicast_addr>")
            return
        try:
            addr = int(parts[2], 0)
        except ValueError:
            print("Usage: mesh configure <unicast_addr>  (e.g. 0x0002)")
            return
        controller.configure_device(addr)

    elif sub == "nodes":
        controller.list_nodes()

    elif sub == "reset":
        controller.reset_network()

    else:
        print(f"Unknown mesh subcommand: {sub}")


def handle_command(state, line):
    """Parse and dispatch a CLI command."""
    parts = line.strip().split()
    if not parts:
        return True

    cmd = parts[0].lower()

    if cmd in ("quit", "exit", "q"):
        return False

    elif cmd == "help":
        print(HELP_TEXT)

    elif cmd == "ble":
        handle_ble_command(state["gatt"], parts)

    elif cmd == "mesh":
        handle_mesh_command(state, parts)

    else:
        print(f"Unknown command: {cmd}. Type 'help' for available commands.")

    return True


def on_stdin(fd, condition, state):
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

        if not handle_command(state, line):
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

    # Shared state passed through GLib callbacks
    state = {
        "bus": bus,
        "gatt": GattController(bus),
        "mesh": None,  # lazy-initialized via 'mesh start'
    }

    # Set stdin to non-blocking
    flags = fcntl.fcntl(sys.stdin.fileno(), fcntl.F_GETFL)
    fcntl.fcntl(sys.stdin.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Print banner
    print(HELP_TEXT)

    # Set up stdin watch
    GLib.io_add_watch(sys.stdin, GLib.IO_IN, on_stdin, state)

    sys.stdout.write("> ")
    sys.stdout.flush()

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
