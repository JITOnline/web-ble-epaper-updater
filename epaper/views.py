import asyncio
import json
import logging
import queue
import subprocess
import threading
import time as _time
from io import BytesIO

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib import messages
from django.core.files.base import ContentFile
from .models import EpaperImage, DeviceConfig
from .forms import EpaperImageForm, DeviceConfigForm

# Import from the manually included gicisky_tag library
from gicisky_tag.scanner import find_device
from bleak import BleakClient

from .calendar import generate_calendar_image
from .ble_logic import (
    run_with_cleanup, get_diagnostic_clients, get_client_lock
)

logger = logging.getLogger(__name__)


def _get_diag_clients():
    return get_diagnostic_clients()


def _get_client_lock():
    return get_client_lock()


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

# ── trigger_update view ──────────────────────────────────────────


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
        asyncio.run(run_with_cleanup(
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

        async with _get_client_lock():
            diag_clients = _get_diag_clients()
            if mac_address in diag_clients:
                client = diag_clients[mac_address]
                if client.is_connected:
                    return JsonResponse({
                        'status': 'success',
                        'message': (
                            f'Already connected to {mac_address}.'
                        ),
                    })
                else:
                    diag_clients.pop(mac_address)

            client = BleakClient(mac_address)
            await client.connect()
            diag_clients[mac_address] = client

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

        async with _get_client_lock():
            diag_clients = _get_diag_clients()
            if mac_address in diag_clients:
                client = diag_clients.pop(mac_address)
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
