import asyncio
import json
import logging
import queue
import subprocess
import threading
import traceback
import time as _time
from io import BytesIO

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib import messages
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, ImageDraw
from .models import EpaperImage, DeviceConfig
from .forms import EpaperImageForm, DeviceConfigForm

# Import from the manually included gicisky_tag library
from gicisky_tag.encoder import encode_image, Dither, TagModel, ColorType
from gicisky_tag.writer import send_data_to_screen
from gicisky_tag.scanner import find_device
from bleak import BleakClient
from bleak.exc import BleakDBusError, BleakDeviceNotFoundError
from .calendar import generate_calendar_image

logger = logging.getLogger(__name__)

# Track active diagnostic connections to MAC addresses
_DIAGNOSTIC_CLIENTS = {}
_CLIENT_LOCK = asyncio.Lock()


class QueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        msg = self.format(record)
        self.q.put(msg)


def index_view(request):
    config = DeviceConfig.get_solo()
    if request.method == 'POST':
        config_form = DeviceConfigForm(request.POST, instance=config)
        if config_form.is_valid():
            config_form.save()
            messages.success(request, 'Configuration updated.')
            return redirect('index')
    else:
        config_form = DeviceConfigForm(instance=config)

    upload_form = EpaperImageForm()
    images = EpaperImage.objects.all().order_by('-uploaded_at')

    context = {
        'config_form': config_form,
        'upload_form': upload_form,
        'images': images,
    }
    return render(request, 'epaper/index.html', context)


def upload_image_view(request):
    if request.method == 'POST':
        form = EpaperImageForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Image/Text uploaded successfully.')
        else:
            messages.error(request, 'Failed to upload image.')
    return redirect('index')


def delete_image_view(request, image_id):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid request method'},
            status=405,
        )
    image_obj = get_object_or_404(EpaperImage, id=image_id)
    if image_obj.image:
        image_obj.image.delete(save=False)
    image_obj.delete()
    messages.success(request, 'Image deleted.')
    return redirect('index')


# ── trigger_update helpers ────────────────────────────────────────

async def _resolve_device(config, msg_queue):
    """Resolve MAC address and raw_type from config or BLE scan.

    Returns (mac_address, raw_type) or (None, None) on failure.
    """
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
    from bleak import BleakScanner
    device = await BleakScanner.find_device_by_address(
        mac_address, timeout=5.0
    )
    if not device:
        msg_queue.put(
            f"WARNING: Device {mac_address} not found in "
            "discovery. Connection might fail if it's sleeping."
        )
    return mac_address, raw_type


def _configure_tag_model(config, raw_type):
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


def _prepare_image(image_obj, tag_model, config):
    """Load or generate the PIL image and apply transforms."""
    if image_obj.image:
        img = Image.open(image_obj.image.path).convert('RGB')
    else:
        img = Image.new(
            'RGB',
            (tag_model.width, tag_model.height),
            color='white',
        )
        draw = ImageDraw.Draw(img)
        text = image_obj.text_overlay or "Hello"
        draw.text(
            (10, tag_model.height // 2 - 10), text, fill='black'
        )
        color_types = [
            ColorType.BWR, ColorType.BWRY, ColorType.BWRGBYO,
        ]
        if tag_model.color_type in color_types:
            draw.text(
                (10, tag_model.height // 2 + 10), text, fill='red'
            )

    if config.rotate:
        img = img.rotate(180, expand=True)
    if config.negative:
        img = ImageOps.invert(img)

    try:
        dither_val = Dither(config.dithering)
    except ValueError:
        dither_val = Dither.NONE

    return encode_image(img, tag_model=tag_model, dithering=dither_val)


# ── trigger_update helpers ────────────────────────────────────────


async def _run_update(image_id, config, msg_queue, gicisky_logger, handler):
    """Perform the full image encode + BLE transfer sequence."""
    mac_address = config.mac_address
    try:
        image_obj = await EpaperImage.objects.aget(id=image_id)
        mac_address, raw_type = await _resolve_device(
            config, msg_queue
        )
        if mac_address is None:
            return

        tag_model = _configure_tag_model(config, raw_type)
        image_data = _prepare_image(image_obj, tag_model, config)

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
        gicisky_logger.removeHandler(handler)
        msg_queue.put(None)


async def _run_with_cleanup(image_id, msg_queue, gicisky_logger, handler):
    """Disconnect stale diagnostic clients, then run the update."""
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
    await _run_update(
        image_id, config, msg_queue, gicisky_logger, handler
    )


def _ndjson_event_stream(msg_queue):
    """Yield newline-delimited JSON messages until sentinel None."""
    while True:
        msg = msg_queue.get()
        if msg is None:
            break
        yield json.dumps({'msg': msg}) + '\n'


# ── trigger_update view ──────────────────────────────────────────

def trigger_update_view(request, image_id):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid request method'},
            status=405,
        )

    detailed_debug = request.GET.get('debug') == '1'
    log_level = logging.DEBUG if detailed_debug else logging.INFO

    msg_queue = queue.Queue()
    handler = QueueHandler(msg_queue)
    handler.setLevel(log_level)

    gicisky_logger = logging.getLogger("gicisky_tag")
    gicisky_logger.setLevel(log_level)
    gicisky_logger.addHandler(handler)

    def thread_worker():
        asyncio.run(_run_with_cleanup(
            image_id, msg_queue, gicisky_logger, handler
        ))

    threading.Thread(target=thread_worker, daemon=True).start()

    return StreamingHttpResponse(
        _ndjson_event_stream(msg_queue),
        content_type='application/x-ndjson',
    )


async def send_cmd_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cmd_hex = data.get('cmd', '').strip()

            if cmd_hex.lower() == 'scan':
                from bleak import BleakScanner
                devices = await BleakScanner.discover(timeout=5.0)
                found = [
                    f"{d.address} ({d.name or 'Unknown'})"
                    for d in devices
                ]
                if found:
                    msg = "Found: " + ", ".join(found)
                else:
                    msg = "No BLE devices found nearby."
                return JsonResponse(
                    {'status': 'success', 'message': msg}
                )

            config = await DeviceConfig.objects.aget(id=1)
            mac_address = config.mac_address
            if not mac_address:
                device_info = await find_device()
                if not device_info:
                    return JsonResponse(
                        {'status': 'error',
                         'message': 'No device found'},
                        status=400,
                    )
                mac_address = device_info["address"]

            cmd_bytes = bytes.fromhex(cmd_hex)
            async with BleakClient(mac_address) as device:
                await device.write_gatt_char(
                    "0000fef1-0000-1000-8000-00805f9b34fb",
                    cmd_bytes,
                    response=True,
                )
            return JsonResponse({
                'status': 'success',
                'message': (
                    f'Successfully sent {cmd_hex} to {mac_address}'
                ),
            })
        except ValueError:
            return JsonResponse(
                {'status': 'error',
                 'message': 'Invalid hex format.'},
                status=400,
            )
        except Exception as e:
            return JsonResponse(
                {'status': 'error', 'message': str(e)}, status=400
            )
    return JsonResponse({'status': 'error'}, status=405)


async def connect_device_view(request):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid method'},
            status=405,
        )

    try:
        config = await DeviceConfig.objects.aget(id=1)
        mac_address = config.mac_address
        if not mac_address:
            return JsonResponse(
                {'status': 'error',
                 'message': 'No MAC address configured'},
                status=400,
            )

        async with _CLIENT_LOCK:
            if mac_address in _DIAGNOSTIC_CLIENTS:
                client = _DIAGNOSTIC_CLIENTS[mac_address]
                if client.is_connected:
                    return JsonResponse({
                        'status': 'success',
                        'message': (
                            f'Already connected to {mac_address}.'
                        ),
                    })
                else:
                    _DIAGNOSTIC_CLIENTS.pop(mac_address)

            client = BleakClient(mac_address)
            await client.connect()
            _DIAGNOSTIC_CLIENTS[mac_address] = client

            await client.write_gatt_char(
                "0000fef1-0000-1000-8000-00805f9b34fb",
                bytes([0x01]),
                response=True,
            )
            return JsonResponse({
                'status': 'success',
                'message': (
                    f'Connected to {mac_address} '
                    '(session persists). Verified with CMD 01.'
                ),
            })
    except Exception as e:
        return JsonResponse(
            {'status': 'error',
             'message': f'Connection failed: {str(e)}'},
            status=400,
        )


async def disconnect_device_view(request):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid method'},
            status=405,
        )

    try:
        config = await DeviceConfig.objects.aget(id=1)
        mac_address = config.mac_address

        async with _CLIENT_LOCK:
            if mac_address in _DIAGNOSTIC_CLIENTS:
                client = _DIAGNOSTIC_CLIENTS.pop(mac_address)
                if client.is_connected:
                    try:
                        await client.unpair()
                    except Exception:
                        pass
                    await client.disconnect()
                    return JsonResponse({
                        'status': 'success',
                        'message': (
                            'Unpaired and disconnected '
                            f'from {mac_address}.'
                        ),
                    })

        return JsonResponse({
            'status': 'success',
            'message': (
                'No active connection for '
                f'{mac_address or "unknown"}.'
            ),
        })
    except Exception as e:
        return JsonResponse(
            {'status': 'error',
             'message': f'Disconnect failed: {str(e)}'},
            status=400,
        )


def bt_reset_view(request):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid method'},
            status=405,
        )
    try:
        subprocess.run(
            ['bluetoothctl', 'power', 'off'],
            capture_output=True, timeout=5,
        )
        _time.sleep(5)
        subprocess.run(
            ['bluetoothctl', 'power', 'on'],
            capture_output=True, timeout=5,
        )
        return JsonResponse(
            {'status': 'success',
             'message': 'Bluetooth adapter restarted.'}
        )
    except Exception as e:
        return JsonResponse(
            {'status': 'error', 'message': f'BT reset failed: {e}'},
            status=500,
        )


def generate_calendar_view(request):
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid request method'},
            status=405,
        )

    config = DeviceConfig.get_solo()
    if not config.ical_url:
        messages.error(
            request,
            'No iCal URL configured. Set it in Settings first.',
        )
        return redirect('index')

    try:
        img = generate_calendar_image(config.ical_url)
    except Exception as e:
        messages.error(
            request, f'Failed to generate calendar image: {e}'
        )
        return redirect('index')

    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    fname = f"calendar_{int(_time.time())}.png"
    epaper_img = EpaperImage()
    epaper_img.image.save(
        fname, ContentFile(buf.read()), save=True
    )

    messages.success(
        request, 'Calendar image generated and added to gallery.'
    )
    return redirect('index')
