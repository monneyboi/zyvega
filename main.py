"""Video Light CLI - Control PL103 video lights via Bluetooth Mesh"""

import asyncio
import logging
import click
from mesh import ZyvegaMesh

mesh = ZyvegaMesh()


def run(coro):
    return asyncio.run(coro)


@click.group()
@click.option('-a', '--address', default=None, help='Target unicast address (hex)')
@click.option('-v', '--verbose', is_flag=True, help='Debug logging')
@click.pass_context
def cli(ctx, address, verbose):
    """Control PL103 video light via Bluetooth Mesh"""
    ctx.ensure_object(dict)
    ctx.obj['addr'] = int(address, 16) if address else None
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format='%(levelname)s: %(message)s'
    )


@cli.command()
@click.pass_context
def on(ctx):
    """Turn light on"""
    if run(mesh.set_power(ctx.obj['addr'], True)):
        click.echo("Light on")
    else:
        raise SystemExit(1)


@cli.command()
@click.pass_context
def off(ctx):
    """Turn light off"""
    if run(mesh.set_power(ctx.obj['addr'], False)):
        click.echo("Light off")
    else:
        raise SystemExit(1)


@cli.command()
@click.argument('level', type=click.IntRange(0, 100))
@click.pass_context
def brightness(ctx, level):
    """Set brightness (0-100%)"""
    if run(mesh.set_brightness(ctx.obj['addr'], level)):
        click.echo(f"Brightness: {level}%")
    else:
        raise SystemExit(1)


@cli.command()
@click.argument('kelvin', type=click.IntRange(2700, 6500))
@click.pass_context
def temp(ctx, kelvin):
    """Set color temperature (2700-6500K)"""
    if run(mesh.set_color_temp(ctx.obj['addr'], kelvin)):
        click.echo(f"Temperature: {kelvin}K")
    else:
        raise SystemExit(1)


@cli.command()
@click.argument('level', type=click.IntRange(0, 100))
@click.argument('r', type=click.IntRange(0, 255))
@click.argument('g', type=click.IntRange(0, 255))
@click.argument('b', type=click.IntRange(0, 255))
@click.pass_context
def rgb(ctx, level, r, g, b):
    """Set RGB color (level r g b)"""
    if run(mesh.set_rgb(ctx.obj['addr'], level, r, g, b)):
        click.echo(f"RGB: {r},{g},{b} @ {level}%")
    else:
        raise SystemExit(1)


@cli.command()
@click.option('-t', '--timeout', default=5.0, help='Scan timeout')
def scan(timeout):
    """Scan for unprovisioned devices"""
    devices = run(mesh.scan_unprovisioned(timeout))
    if not devices:
        click.echo("No devices found")
        return
    click.echo(f"Found {len(devices)} device(s):")
    for d in devices:
        click.echo(f"  {d.get('name', '?'):20} RSSI:{d.get('rssi', '?'):4}  {d['uuid']}")


@cli.command()
@click.argument('uuid', required=False)
def setup(uuid):
    """Provision and configure a device"""
    click.echo("Setting up device..." + (f" ({uuid})" if uuid else ""))
    if run(mesh.setup_device(uuid)):
        click.echo("Setup complete")
    else:
        raise SystemExit(1)


@cli.command('list')
def list_nodes():
    """List provisioned nodes"""
    nodes = run(mesh.list_nodes())
    if not nodes:
        click.echo("No nodes")
        return
    for n in nodes:
        click.echo(f"  0x{n['unicast_address']:04x}  {n['uuid'][:16]}...")


@cli.command()
@click.argument('addr')
def remove(addr):
    """Remove a node by unicast address"""
    unicast = int(addr, 16)
    if run(mesh.remove_node(unicast)):
        click.echo(f"Removed 0x{unicast:04x}")
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
