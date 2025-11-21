"""Video Light CLI - Control PL103 video lights via Bluetooth Mesh"""

import asyncio
import logging
import click
from mesh_provisioner import MeshProvisioner
from mesh_control import MeshLightController


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
@click.pass_context
def brightness(ctx, brightness):
    """Set brightness (0-100%) via mesh"""
    try:
        controller = MeshLightController()
        if controller.set_brightness(brightness, ctx.obj['address']):
            click.echo(f"✓ Brightness set to {brightness}%")
        else:
            click.echo("✗ Failed to set brightness", err=True)
            ctx.exit(1)
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument('kelvin', type=click.IntRange(2700, 6500))
@click.pass_context
def temp(ctx, kelvin):
    """Set color temperature (2700-6500K) via mesh"""
    try:
        controller = MeshLightController()
        if controller.set_color_temp(kelvin, ctx.obj['address']):
            click.echo(f"✓ Color temperature set to {kelvin}K")
        else:
            click.echo("✗ Failed to set color temperature", err=True)
            ctx.exit(1)
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument('brightness', type=click.IntRange(0, 100))
@click.argument('r', type=click.IntRange(0, 255))
@click.argument('g', type=click.IntRange(0, 255))
@click.argument('b', type=click.IntRange(0, 255))
@click.pass_context
def rgb(ctx, brightness, r, g, b):
    """Set RGB color (brightness: 0-100, r/g/b: 0-255) via mesh"""
    try:
        controller = MeshLightController()
        if controller.set_rgb(brightness, r, g, b, ctx.obj['address']):
            click.echo(f"✓ RGB set to ({r}, {g}, {b}) at {brightness}%")
        else:
            click.echo("✗ Failed to set RGB", err=True)
            ctx.exit(1)
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument('state', type=click.Choice(['on', 'off'], case_sensitive=False))
@click.pass_context
def power(ctx, state):
    """Turn light on or off via mesh"""
    try:
        controller = MeshLightController()
        on = state.lower() == 'on'
        if controller.set_power(on, ctx.obj['address']):
            click.echo(f"✓ Light turned {state}")
        else:
            click.echo(f"✗ Failed to turn light {state}", err=True)
            ctx.exit(1)
    except Exception as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.pass_context
def scan(ctx):
    """Scan for unprovisioned PL103 devices (alias for 'mesh scan')"""
    async def scan_devices():
        provisioner = MeshProvisioner()
        devices = await provisioner.scan_unprovisioned(timeout=5.0)

        if not devices:
            click.echo("No unprovisioned mesh devices found")
            ctx.exit(1)

        click.echo(f"\nFound {len(devices)} unprovisioned device(s):")
        for device in devices:
            rssi_str = f"RSSI: {device['rssi']} dBm" if device['rssi'] else ""
            click.echo(f"  {device['name']:20s} {device['address']} {rssi_str}")

    run_async(scan_devices())


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
