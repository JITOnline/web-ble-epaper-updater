import asyncio
from bleak import BleakScanner
from gicisky_tag.log import logger


async def find_device():
    device_info = None

    def scan_callback(device, data):
        nonlocal device_info
        # Look for manufacturer data with Gicisky/Picksmart IDs
        # 20563 = 0x5053, 385 = 0x0181
        m_ids = [20563, 385]
        found_id = None
        for m_id in m_ids:
            if m_id in data.manufacturer_data:
                found_id = m_id
                break

        if found_id is not None:
            address = device.address.upper()
            logger.debug(f"Device {device}: {data}")
            m_data = data.manufacturer_data[found_id]

            # HTML: rawType = (data.getUint8(4) << 8) | data.getUint8(0);
            raw_type = None
            if len(m_data) >= 5:
                raw_type = (m_data[4] << 8) | m_data[0]
            elif len(m_data) >= 1:
                raw_type = m_data[0]

            power_data = None
            if len(m_data) >= 2:
                power_data = float(m_data[1]) / 10

            raw_str = f'{raw_type:04x}' if raw_type is not None else 'N/A'
            bat_str = f'{power_data:.1f} V' if power_data is not None else 'N/A'
            logger.info(
                f"Found device {address} "
                f"(ID: {found_id:04x}, rawType: {raw_str}). "
                f"Battery: {bat_str}"
            )
            device_info = {"address": address, "raw_type": raw_type}

    scanner = BleakScanner(scan_callback)
    while device_info is None:
        await scanner.start()
        await asyncio.sleep(1.0)
        await scanner.stop()

    return device_info
