"""Video Light CLI - Control PL103 video lights via Bluetooth"""

import asyncio
import logging
import click
from videolight_control import VideoLightController
from mesh_provisioner import MeshProvisioner


def run_async(coro):
    """Helper to run async functions in Click commands"""
    return asyncio.run(coro)


@click.group()
@click.option('--address', '-a', default=None, help='Device MAC address (auto-scan if not provided)')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose debug logging')
@click.pass_context
def cli(ctx, address, verbose):
    """Control PL103 video light via Bluetooth"""
    ctx.ensure_object(dict)
    ctx.obj['address'] = address

    # Setup logging
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logging.getLogger('bleak').setLevel(logging.DEBUG)
    else:
        logging.basicConfig(
            level=logging.WARNING,
            format='%(levelname)s: %(message)s'
        )


@cli.command()
@click.argument('brightness', type=click.IntRange(0, 100))
@click.option('--device-id', '-d', default=0x0380, help='Device ID (default: 0x0380)')
@click.pass_context
def brightness(ctx, brightness, device_id):
    """Set brightness (0-100%)"""
    async def set_brightness():
        controller = VideoLightController()
        if await controller.connect(ctx.obj['address']):
            await controller.set_brightness(brightness, device_id)
            await controller.disconnect()
        else:
            click.echo("Failed to connect to device", err=True)
            ctx.exit(1)

    run_async(set_brightness())


@cli.command()
@click.argument('kelvin', type=click.IntRange(2700, 6500))
@click.option('--device-id', '-d', default=0x0380, help='Device ID (default: 0x0380)')
@click.pass_context
def temp(ctx, kelvin, device_id):
    """Set color temperature (2700-6500K)"""
    async def set_temp():
        controller = VideoLightController()
        if await controller.connect(ctx.obj['address']):
            await controller.set_color_temp(kelvin, device_id)
            await controller.disconnect()
        else:
            click.echo("Failed to connect to device", err=True)
            ctx.exit(1)

    run_async(set_temp())


@cli.command()
@click.argument('brightness', type=click.IntRange(0, 100))
@click.argument('r', type=click.IntRange(0, 255))
@click.argument('g', type=click.IntRange(0, 255))
@click.argument('b', type=click.IntRange(0, 255))
@click.option('--device-id', '-d', default=0x0380, help='Device ID (default: 0x0380)')
@click.pass_context
def rgb(ctx, brightness, r, g, b, device_id):
    """Set RGB color (brightness: 0-100, r/g/b: 0-255)"""
    async def set_rgb():
        controller = VideoLightController()
        if await controller.connect(ctx.obj['address']):
            await controller.set_rgb(brightness, r, g, b, device_id)
            await controller.disconnect()
        else:
            click.echo("Failed to connect to device", err=True)
            ctx.exit(1)

    run_async(set_rgb())


@cli.command()
@click.pass_context
def scan(ctx):
    """Scan for available PL103 devices"""
    async def scan_devices():
        controller = VideoLightController()
        address = await controller.scan_for_device()
        if address:
            click.echo(f"Found device at: {address}")
        else:
            click.echo("No PL103 devices found", err=True)
            ctx.exit(1)

    run_async(scan_devices())


@cli.command()
@click.pass_context
def interactive(ctx):
    """Interactive mode - stay connected and send multiple commands"""
    async def interactive_mode():
        controller = VideoLightController()
        if not await controller.connect(ctx.obj['address']):
            click.echo("Failed to connect to device", err=True)
            ctx.exit(1)

        click.echo("Connected! Available commands:")
        click.echo("  b <0-100>           - Set brightness")
        click.echo("  t <2700-6500>       - Set color temperature")
        click.echo("  rgb <0-100> R G B   - Set RGB color")
        click.echo("  q or quit           - Quit")

        try:
            while True:
                try:
                    user_input = input("\n> ").strip()
                    if not user_input:
                        continue

                    parts = user_input.split()
                    cmd = parts[0].lower()

                    if cmd in ['q', 'quit', 'exit']:
                        break
                    elif cmd == 'b' and len(parts) == 2:
                        brightness = int(parts[1])
                        if 0 <= brightness <= 100:
                            await controller.set_brightness(brightness)
                            click.echo(f"Set brightness to {brightness}%")
                        else:
                            click.echo("Brightness must be 0-100", err=True)
                    elif cmd == 't' and len(parts) == 2:
                        temp = int(parts[1])
                        if 2700 <= temp <= 6500:
                            await controller.set_color_temp(temp)
                            click.echo(f"Set color temperature to {temp}K")
                        else:
                            click.echo("Temperature must be 2700-6500K", err=True)
                    elif cmd == 'rgb' and len(parts) == 5:
                        brightness = int(parts[1])
                        r, g, b = int(parts[2]), int(parts[3]), int(parts[4])
                        if (0 <= brightness <= 100 and
                            0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
                            await controller.set_rgb(brightness, r, g, b)
                            click.echo(f"Set RGB to ({r}, {g}, {b}) at {brightness}%")
                        else:
                            click.echo("Invalid RGB values", err=True)
                    else:
                        click.echo("Unknown command. Try: b <0-100>, t <2700-6500>, rgb <0-100> R G B, or q to quit", err=True)

                except ValueError:
                    click.echo("Invalid number format", err=True)
                except Exception as e:
                    click.echo(f"Error: {e}", err=True)

        finally:
            await controller.disconnect()

    run_async(interactive_mode())


# Mesh Provisioning Commands
@cli.group()
def mesh():
    """Bluetooth Mesh provisioning commands"""
    pass


@mesh.command('scan')
@click.option('--timeout', '-t', default=5.0, help='Scan timeout in seconds')
@click.pass_context
def mesh_scan(ctx, timeout):
    """Scan for unprovisioned mesh devices"""
    async def scan():
        provisioner = MeshProvisioner()
        devices = await provisioner.scan_unprovisioned(timeout)

        if not devices:
            click.echo("No unprovisioned mesh devices found")
            return

        click.echo(f"\nFound {len(devices)} unprovisioned device(s):")
        for device in devices:
            rssi_str = f"RSSI: {device['rssi']} dBm" if device['rssi'] else ""
            click.echo(f"  {device['name']:20s} {device['address']} {rssi_str}")
            if device['uuid']:
                click.echo(f"    UUID: {device['uuid']}")

    run_async(scan())


@mesh.command('setup')
@click.option('--address', '-a', default=None, help='Device MAC address (auto-scan if not provided)')
@click.pass_context
def mesh_setup(ctx, address):
    """Setup (provision and configure) a mesh device"""
    async def setup():
        provisioner = MeshProvisioner()

        click.echo("Starting mesh device setup...")
        if address:
            click.echo(f"Target device: {address}")
        else:
            click.echo("Scanning for PL103 devices...")

        if await provisioner.setup_device(address):
            click.echo("\n✓ Device setup complete!")
            click.echo("The device is now part of the mesh network.")
        else:
            click.echo("\n✗ Device setup failed", err=True)
            ctx.exit(1)

    run_async(setup())


@mesh.command('list')
@click.pass_context
def mesh_list(ctx):
    """List all provisioned mesh nodes"""
    provisioner = MeshProvisioner()
    nodes = provisioner.list_nodes()

    if not nodes:
        click.echo("No provisioned nodes found")
        return

    click.echo(f"\nProvisioned nodes ({len(nodes)}):")
    for node in nodes:
        addr = node.get('unicast_address')
        click.echo(f"  {node['address']:20s} @ 0x{addr:04x}")


@mesh.command('remove')
@click.argument('address')
@click.pass_context
def mesh_remove(ctx, address):
    """Remove a node from the mesh network"""
    provisioner = MeshProvisioner()

    click.echo(f"Removing node: {address}")
    if provisioner.remove_node(address):
        click.echo("✓ Node removed successfully")
    else:
        click.echo("✗ Failed to remove node", err=True)
        ctx.exit(1)


@mesh.command('teardown')
@click.option('--confirm', is_flag=True, help='Skip confirmation prompt')
@click.pass_context
def mesh_teardown(ctx, confirm):
    """Destroy the mesh network and remove all nodes"""
    if not confirm:
        click.echo("This will remove all provisioned nodes and destroy the mesh network.")
        if not click.confirm("Are you sure?"):
            click.echo("Aborted")
            return

    provisioner = MeshProvisioner()

    click.echo("Tearing down mesh network...")
    if provisioner.destroy_network():
        click.echo("✓ Mesh network destroyed")
    else:
        click.echo("✗ Failed to teardown network", err=True)
        ctx.exit(1)


if __name__ == "__main__":
    cli()
