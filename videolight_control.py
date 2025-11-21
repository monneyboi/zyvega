"""Video Light (PL103) Control Module - Python port
Based on reverse-engineered protocol
"""

import struct
from typing import List, Optional
from bleak import BleakClient, BleakScanner


class CrcCheck:
    """CRC checksum calculator for video light protocol"""

    LOOKUP_TABLE = [
        0, 4129, 8258, 12387, 16516, 20645, 24774, 28903, 33032, 37161, 41290, 45419,
        49548, 53677, 57806, 61935, 4657, 528, 12915, 8786, 21173, 17044, 29431, 25302,
        37689, 33560, 45947, 41818, 54205, 50076, 62463, 58334, 9314, 13379, 1056, 5121,
        25830, 29895, 17572, 21637, 42346, 46411, 34088, 38153, 58862, 62927, 50604,
        54669, 13907, 9842, 5649, 1584, 30423, 26358, 22165, 18100, 46939, 42874, 38681,
        34616, 63455, 59390, 55197, 51132, 18628, 22757, 26758, 30887, 2112, 6241, 10242,
        14371, 51660, 55789, 59790, 63919, 35144, 39273, 43274, 47403, 23285, 19156,
        31415, 27286, 6769, 2640, 14899, 10770, 56317, 52188, 64447, 60318, 39801, 35672,
        47931, 43802, 27814, 31879, 19684, 23749, 11298, 15363, 3168, 7233, 60846, 64911,
        52716, 56781, 44330, 48395, 36200, 40265, 32407, 28342, 24277, 20212, 15891,
        11826, 7761, 3696, 65439, 61374, 57309, 53244, 48923, 44858, 40793, 36728, 37256,
        33193, 45514, 41451, 53516, 49453, 61774, 57711, 4224, 161, 12482, 8419, 20484,
        16421, 28742, 24679, 33721, 37784, 41979, 46042, 49981, 54044, 58239, 62302, 689,
        4752, 8947, 13010, 16949, 21012, 25207, 29270, 46570, 42443, 38312, 34185, 62830,
        58703, 54572, 50445, 13538, 9411, 5280, 1153, 29798, 25671, 21540, 17413, 42971,
        47098, 34713, 38840, 59231, 63358, 50973, 55100, 9939, 14066, 1681, 5808, 26199,
        30326, 17941, 22068, 55628, 51565, 63758, 59695, 39368, 35305, 47498, 43435,
        22596, 18533, 30726, 26663, 6336, 2273, 14466, 10403, 52093, 56156, 60223, 64286,
        35833, 39896, 43963, 48026, 19061, 23124, 27191, 31254, 2801, 6864, 10931, 14994,
        64814, 60687, 56684, 52557, 48554, 44427, 40424, 36297, 31782, 27655, 23652,
        19525, 15522, 11395, 7392, 3265, 61215, 65342, 53085, 57212, 44955, 49082, 36825,
        40952, 28183, 32310, 20053, 24180, 11923, 16050, 3793, 7920
    ]

    @staticmethod
    def calc_checksum(data: List[int]) -> int:
        """Calculate CRC16 checksum for given data"""
        crc = 0
        for byte in data:
            i3 = crc >> 8
            crc = ((crc << 8) & 0xFFFF) ^ (CrcCheck.LOOKUP_TABLE[(byte & 0xFF) ^ i3] & 0xFFFF)
        return crc


class VideoLightCommand:
    """Video light command builder"""

    def __init__(self, sequence: int, message_type: int, payload: List[int] = None):
        """
        Create a video light command

        Message structure:
        - Header: 0x24 0x3C
        - Length: N (data section length)
        - Unknown: 0x00
        - Data Section:
          - Field1: 0x01 0x00 (big-endian)
          - Sequence: 2 bytes (big-endian)
          - Message Type: 2 bytes (big-endian)
          - Payload: variable
        - CRC: 2 bytes (little-endian)
        """
        if payload is None:
            payload = []

        field1 = [0x01, 0x00]
        seq = [(sequence >> 8) & 0xFF, sequence & 0xFF]
        msg_type = [(message_type >> 8) & 0xFF, message_type & 0xFF]

        data_section = field1 + seq + msg_type + payload
        length = len(data_section)

        crc = CrcCheck.calc_checksum(data_section)
        crc_low = crc & 0xFF
        crc_high = (crc >> 8) & 0xFF

        self.message = [0x24, 0x3C, length, 0x00] + data_section + [crc_low, crc_high]

    def get_bytes(self) -> bytes:
        """Get command as bytes"""
        return bytes(self.message)

    def __str__(self) -> str:
        """Get command as hex string"""
        return '-'.join(f'{b:02X}' for b in self.message)


# Command Types (from ZYLightClient.smali)
CMD_LIGHT = 0x1001        # Brightness/Power control
CMD_COLOR_TEMP = 0x1002   # Color temperature control
CMD_RGB = 0x1003          # RGB control
CMD_HUE = 0x1004          # Hue control
CMD_SAT = 0x1005          # Saturation control
CMD_CMY = 0x1006          # CMY control
CMD_CHMCOOR = 0x1007      # Chromatic coordinate control


class VideoLightController:
    """Video light controller using Bluetooth LE"""

    SERVICE_UUID = "0000fee9-0000-1000-8000-00805f9b34fb"
    WRITE_CHAR_UUID = "d44bc439-abfd-45a2-b575-925416129600"
    NOTIFY_CHAR_UUID = "d44bc439-abfd-45a2-b575-925416129601"

    def __init__(self):
        self.sequence = 1
        self.client: Optional[BleakClient] = None
        self.device_address: Optional[str] = None

    async def scan_for_device(self) -> Optional[str]:
        """Scan for PL103 video light"""
        print("Scanning for PL103 video light...")
        devices = await BleakScanner.discover(timeout=5.0)

        for device in devices:
            if device.name and device.name.startswith("PL103"):
                print(f"Found device: {device.name} ({device.address})")
                return device.address

        return None

    async def connect(self, address: Optional[str] = None) -> bool:
        """
        Connect to video light

        Args:
            address: Device MAC address (if None, will scan for device)

        Returns:
            True if connected successfully
        """
        try:
            if address is None:
                address = await self.scan_for_device()
                if address is None:
                    print("No PL103 device found")
                    return False

            self.device_address = address
            print(f"Connecting to {address}...")

            self.client = BleakClient(address)
            await self.client.connect()

            # Enable notifications
            await self.client.start_notify(self.NOTIFY_CHAR_UUID, self._handle_notification)

            print("Connected to video light!")
            return True

        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def _handle_notification(self, sender, data: bytearray):
        """Handle notification from device"""
        hex_string = '-'.join(f'{b:02X}' for b in data)
        print(f"Received: {hex_string}")

    async def send_command(self, cmd: VideoLightCommand):
        """Send command to device"""
        if self.client is None or not self.client.is_connected:
            raise RuntimeError("Not connected to device")

        print(f"Sending: {cmd}")
        await self.client.write_gatt_char(self.WRITE_CHAR_UUID, cmd.get_bytes())
        self.sequence += 1

    async def set_brightness(self, brightness: int, device_id: int = 0x0380):
        """
        Set brightness (0-100%)

        Args:
            brightness: Brightness percentage (0-100)
            device_id: Device ID (typically 0x0380 / 896 decimal)
        """
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness must be between 0 and 100")

        # Convert percentage to value (0-10000 for finer control)
        value = round(brightness * 100)

        # Payload format: [deviceId_high, deviceId_low, value_high, value_low]
        payload = [
            (device_id >> 8) & 0xFF,
            device_id & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF
        ]

        cmd = VideoLightCommand(self.sequence, CMD_LIGHT, payload)
        await self.send_command(cmd)

    async def set_color_temp(self, kelvin: int, device_id: int = 0x0380):
        """
        Set color temperature in Kelvin (2700-6500)

        Args:
            kelvin: Color temperature (2700-6500K)
            device_id: Device ID (typically 0x0380 / 896 decimal)
        """
        if not 2700 <= kelvin <= 6500:
            raise ValueError("Color temperature must be between 2700K and 6500K")

        # Payload format: [deviceId_high, deviceId_low, kelvin_high, kelvin_low]
        payload = [
            (device_id >> 8) & 0xFF,
            device_id & 0xFF,
            (kelvin >> 8) & 0xFF,
            kelvin & 0xFF
        ]

        cmd = VideoLightCommand(self.sequence, CMD_COLOR_TEMP, payload)
        await self.send_command(cmd)

    async def set_rgb(self, brightness: int, r: int, g: int, b: int, device_id: int = 0x0380):
        """
        Set RGB color

        Args:
            brightness: Brightness percentage (0-100)
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
            device_id: Device ID (typically 0x0380)
        """
        if not 0 <= brightness <= 100:
            raise ValueError("Brightness must be between 0 and 100")
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            raise ValueError("RGB values must be between 0 and 255")

        brightness_value = round(brightness * 100)

        payload = [
            (device_id >> 8) & 0xFF,
            device_id & 0xFF,
            (brightness_value >> 8) & 0xFF,
            brightness_value & 0xFF,
            r & 0xFF,
            g & 0xFF,
            b & 0xFF
        ]

        cmd = VideoLightCommand(self.sequence, CMD_RGB, payload)
        await self.send_command(cmd)

    async def disconnect(self):
        """Disconnect from device"""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected")
