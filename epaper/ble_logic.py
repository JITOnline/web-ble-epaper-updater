import asyncio
import logging
import traceback
from PIL import Image, ImageOps, ImageDraw
from gicisky_tag.encoder import encode_image, Dither, TagModel, ColorType
from gicisky_tag.writer import send_data_to_screen
from gicisky_tag.scanner import find_device
from bleak import BleakScanner
from bleak.exc import BleakDBusError, BleakDeviceNotFoundError
from .models import EpaperImage

logger = logging.getLogger(__name__)

# Track active diagnostic connections to MAC addresses
_DIAGNOSTIC_CLIENTS = {}
_CLIENT_LOCK = asyncio.Lock()


async def resolve_device(config, msg_queue):
    """Resolve MAC address and raw_type from config or BLE scan."""
    mac_address = config.mac_address
    raw_type = None

    if not mac_address:
        msg_queue.put("Scanning for nearby E-Paper tags...")
        device_info = await find_device()
        if not device_info:
            msg_queue.put(
                "ERROR: No Tag Found nearby. "
                "Please specify MAC Address manually "
                "if scanning fails."
            )
            return None, None
        return device_info["address"], device_info["raw_type"]

    # Pre-scan to "warm up" BlueZ cache
    msg_queue.put(f"Waking up connection for {mac_address}...")
    device = await BleakScanner.find_device_by_address(
        mac_address, timeout=5.0
    )
    if not device:
        msg_queue.put(
            f"WARNING: Device {mac_address} not found in "
            "discovery. Connection might fail if it's sleeping."
        )
    return mac_address, raw_type


def configure_tag_model(config, raw_type):
    """Build a TagModel from config and optional raw_type."""
    if config.raw_type:
        raw_type = int(config.raw_type, 16)
    tag_model = TagModel(raw_type)

    if config.width_override:
        tag_model.width = config.width_override
    if config.height_override:
        tag_model.height = config.height_override
    tag_model.mirror_image = config.force_mirror
    tag_model.use_compression = config.force_compression

    if config.force_second_color:
        if tag_model.color_type == ColorType.BW:
            tag_model.color_type = ColorType.BWR
    else:
        tag_model.color_type = ColorType.BW

    return tag_model


def prepare_image(image_obj, tag_model, config):
    """Load or generate the PIL image and apply transforms."""
    if image_obj.image:
        img = Image.open(image_obj.image.path).convert("RGB")
    else:
        img = Image.new(
            "RGB",
            (tag_model.width, tag_model.height),
            color="white",
        )
        draw = ImageDraw.Draw(img)
        text = image_obj.text_overlay or "Hello"
        draw.text((10, tag_model.height // 2 - 10), text, fill="black")
        color_types = [
            ColorType.BWR,
            ColorType.BWRY,
            ColorType.BWRGBYO,
        ]
        if tag_model.color_type in color_types:
            draw.text((10, tag_model.height // 2 + 10), text, fill="red")

    if config.rotate:
        img = img.rotate(180, expand=True)
    if config.negative:
        img = ImageOps.invert(img)

    try:
        dither_val = Dither(config.dithering)
    except ValueError:
        dither_val = Dither.NONE

    return encode_image(img, tag_model=tag_model, dithering=dither_val)


async def perform_update(
    image_id, config, msg_queue, gicisky_logger, handler
):
    """Perform the full image encode + BLE transfer sequence."""
    mac_address = config.mac_address
    try:
        image_obj = await EpaperImage.objects.aget(id=image_id)
        mac_address, raw_type = await resolve_device(config, msg_queue)
        if mac_address is None:
            return

        tag_model = configure_tag_model(config, raw_type)
        image_data = prepare_image(image_obj, tag_model, config)

        msg_queue.put(f"Starting transfer to {mac_address}...")
        await send_data_to_screen(mac_address, image_data)

        await asyncio.sleep(0.5)
        msg_queue.put(
            f"SUCCESS: Image successfully transferred "
            f"to MAC {mac_address}!"
        )
    except BleakDeviceNotFoundError:
        msg_queue.put(
            f"ERROR: Device {mac_address} not found. "
            "Make sure the tag is powered on and nearby, "
            "then try again."
        )
    except BleakDBusError as e:
        msg_queue.put(
            f"ERROR: Bluetooth adapter error: {e}. "
            "Try clicking 'Reset Bluetooth' and retrying."
        )
    except Exception as e:
        traceback.print_exc()
        msg_queue.put(f"ERROR: {str(e)}")
    finally:
        if handler in gicisky_logger.handlers:
            gicisky_logger.removeHandler(handler)
        msg_queue.put(None)


async def run_with_cleanup(image_id, msg_queue, gicisky_logger, handler):
    """Disconnect stale diagnostic clients, then run the update."""
    from .models import DeviceConfig

    config = await DeviceConfig.objects.aget(id=1)
    mac_addr = config.mac_address
    if not mac_addr:
        msg_queue.put("ERROR: No MAC address configured.")
        msg_queue.put(None)
        return

    async with _CLIENT_LOCK:
        if mac_addr in _DIAGNOSTIC_CLIENTS:
            try:
                old_client = _DIAGNOSTIC_CLIENTS.pop(mac_addr)
                await old_client.disconnect()
            except Exception:
                pass
    await perform_update(image_id, config, msg_queue, gicisky_logger, handler)


async def disconnect_diagnostic(mac_addr):
    """Force disconnect diagnostic client for a MAC."""
    async with _CLIENT_LOCK:
        if mac_addr in _DIAGNOSTIC_CLIENTS:
            try:
                client = _DIAGNOSTIC_CLIENTS.pop(mac_addr)
                await client.disconnect()
            except Exception:
                pass


def get_diagnostic_clients():
    return _DIAGNOSTIC_CLIENTS


def get_client_lock():
    return _CLIENT_LOCK
