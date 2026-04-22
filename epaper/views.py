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
    run_with_cleanup,
    get_diagnostic_clients,
    get_client_lock,
)
from .automation import (
    set_automation_cron,
    check_and_update_automation,
)

from .ai_image import generate_ai_image

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
    old_automation = config.automation_enabled

    if request.method == "POST":
        config_form = DeviceConfigForm(request.POST, instance=config)
        if config_form.is_valid():
            config_form.save()
            messages.success(request, "Configuration updated.")

            # Management automation cron job
            if config.automation_enabled != old_automation:
                set_automation_cron(config.automation_enabled)
                status = "ENABLED" if config.automation_enabled else "DISABLED"
                request.session["automation_alert"] = status
                if config.automation_enabled:
                    threading.Thread(
                        target=check_and_update_automation, daemon=True
                    ).start()

            return redirect("index")
    else:
        config_form = DeviceConfigForm(instance=config)

    upload_form = EpaperImageForm()
    # List images by uploaded_at desc
    images_list = list(EpaperImage.objects.all().order_by("uploaded_at"))
    # Pre-calculate numbers so we don't rely on forloop.revindex if it fails
    # i=0 is oldest. num=1.
    for i, img in enumerate(images_list):
        img.num = i + 1

    context = {
        "config_form": config_form,
        "upload_form": upload_form,
        "images": images_list[::-1],  # Newest first in view
        "automation_alert": request.session.pop("automation_alert", None),
        "last_failed_prompt": request.session.pop("last_failed_prompt", ""),
    }
    return render(request, "epaper/index.html", context)


def upload_image_view(request):
    if request.method == "POST":
        form = EpaperImageForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Image/Text uploaded successfully.")
        else:
            messages.error(request, "Failed to upload image.")
    return redirect("index")


def delete_image_view(request, image_id):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid request method"},
            status=405,
        )
    image_obj = get_object_or_404(EpaperImage, id=image_id)
    if image_obj.image:
        image_obj.image.delete(save=False)
    image_obj.delete()
    messages.success(request, "Image deleted.")
    return redirect("index")


# ── trigger_update helpers ────────────────────────────────────────

# ── trigger_update view ──────────────────────────────────────────


def _ndjson_event_stream(msg_queue):
    """Yield newline-delimited JSON messages until sentinel None."""
    # Yield initial message to force Gunicorn to see activity immediately
    yield json.dumps(
        {"msg": "Connection established. Waiting for BLE sequence..."}
    ) + "\n"
    while True:
        try:
            # Wake up according to user-requested 120s timeout
            msg = msg_queue.get(timeout=120.0)
            if msg is None:
                break
            yield json.dumps({"msg": msg}) + "\n"
        except queue.Empty:
            # Keepalive JSON to avoid browser/proxy timeouts
            yield json.dumps({"keepalive": True}) + "\n"
        except Exception as e:
            yield json.dumps({"msg": f"ERROR: Stream error: {str(e)}"}) + "\n"
            break


# ── trigger_update view ──────────────────────────────────────────


def trigger_update_view(request, image_id):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid request method"},
            status=405,
        )

    detailed_debug = request.GET.get("debug") == "1"
    log_level = logging.DEBUG if detailed_debug else logging.INFO

    msg_queue = queue.Queue()
    handler = QueueHandler(msg_queue)
    handler.setLevel(log_level)

    gicisky_logger = logging.getLogger("gicisky_tag")
    gicisky_logger.setLevel(log_level)
    gicisky_logger.addHandler(handler)

    def thread_worker():
        asyncio.run(run_with_cleanup(image_id, msg_queue, gicisky_logger, handler))

    threading.Thread(target=thread_worker, daemon=True).start()

    return StreamingHttpResponse(
        _ndjson_event_stream(msg_queue),
        content_type="application/x-ndjson",
    )


async def _find_device_robust(mac_address, timeout=10.0, detailed_debug=False):
    """
    Highly robust way to find a BLE device on Linux/BlueZ.
    Tries targeted scan, then full discovery.
    """
    from bleak import BleakScanner

    import traceback
    import subprocess

    if detailed_debug:
        logger.info(f"[DEBUG] Robust Search for {mac_address} (Timeout: {timeout}s)")

    # 1. Try targeted scan with active mode
    scanner_kwargs = {"scanning_mode": "active"}
    device_obj = await BleakScanner.find_device_by_address(
        mac_address, timeout=timeout, **scanner_kwargs
    )
    if device_obj:
        if detailed_debug:
            logger.info(f"[DEBUG] Found {mac_address} via targeted scan.")
        return device_obj

    if detailed_debug:
        logger.info(
            f"[DEBUG] Targeted scan failed for {mac_address}. Trying full discovery..."
        )

    # 2. Try full discovery with active mode
    try:
        devices = await BleakScanner.discover(timeout=timeout, **scanner_kwargs)
        for d in devices:
            if detailed_debug:
                logger.info(f"[DEBUG] Discovered: {d.address} ({d.name or 'Unknown'})")
            if d.address.upper() == mac_address.upper():
                if detailed_debug:
                    logger.info(f"[DEBUG] Match found in full discovery: {d.address}")
                return d
    except Exception as e:
        if detailed_debug:
            logger.error(f"[DEBUG] Discovery error: {traceback.format_exc()}")

    # 3. Fallback: Force BlueZ to recognize the device using bluetoothctl
    if detailed_debug:
        logger.info(
            f"[DEBUG] Bleak failed to find {mac_address}. Trying bluetoothctl fallback..."
        )
    try:
        # Run a brief scan using bluetoothctl to populate D-Bus
        subprocess.run(
            ["bluetoothctl", "--timeout", "5", "scan", "on"], capture_output=True
        )
        # Check if D-Bus now knows about it
        info = subprocess.run(
            ["bluetoothctl", "info", mac_address], capture_output=True, text=True
        )
        if detailed_debug:
            logger.info(f"[DEBUG] bluetoothctl info returned: {info.stdout.strip()}")

        # Give Bleak one last chance now that D-Bus might have it
        device_obj = await BleakScanner.find_device_by_address(mac_address, timeout=3.0)
        if device_obj:
            if detailed_debug:
                logger.info(f"[DEBUG] Found {mac_address} after bluetoothctl scan.")
            return device_obj
    except Exception as e:
        if detailed_debug:
            logger.error(f"[DEBUG] bluetoothctl fallback error: {str(e)}")

    return None


async def send_cmd_view(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            cmd_hex = data.get("cmd", "").strip()

            if cmd_hex.lower() == "scan":
                from bleak import BleakScanner

                devices = await BleakScanner.discover(timeout=5.0)
                found = [f"{d.address} ({d.name or 'Unknown'})" for d in devices]
                if found:
                    msg = "Found: " + ", ".join(found)
                else:
                    msg = "No BLE devices found nearby."
                return JsonResponse({"status": "success", "message": msg})

            config = await DeviceConfig.objects.aget(id=1)
            mac_address = config.mac_address
            if not mac_address:
                device_info = await find_device()
                if not device_info:
                    return JsonResponse(
                        {"status": "error", "message": "No device found"},
                        status=400,
                    )
                mac_address = device_info["address"]

            cmd_bytes = bytes.fromhex(cmd_hex)

            detailed_debug = request.GET.get("debug") == "1"
            device_obj = await _find_device_robust(
                mac_address, detailed_debug=detailed_debug
            )
            if not device_obj:
                # One last attempt: direct address connect anyway
                logger.warning(
                    f"Device {mac_address} not found in scan."
                    " Attempting direct connect..."
                )
                device_obj = mac_address

            async with BleakClient(device_obj, timeout=30.0) as device:
                await device.write_gatt_char(
                    "0000fef1-0000-1000-8000-00805f9b34fb",
                    cmd_bytes,
                    response=True,
                )
            return JsonResponse(
                {
                    "status": "success",
                    "message": (f"Successfully sent {cmd_hex} to {mac_address}"),
                }
            )
        except ValueError:
            return JsonResponse(
                {"status": "error", "message": "Invalid hex format."},
                status=400,
            )
        except Exception as e:
            detailed_debug = request.GET.get("debug") == "1"
            msg = str(e)
            if detailed_debug:
                import traceback

                msg = f"{msg}\n{traceback.format_exc()}"
            return JsonResponse({"status": "error", "message": msg}, status=400)
    return JsonResponse({"status": "error"}, status=405)


async def connect_device_view(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid method"}, status=405
        )

    try:
        from asgiref.sync import sync_to_async

        config = await sync_to_async(DeviceConfig.get_solo)()

        # Priority: explicit MAC from request body, then database
        mac_address = None
        try:
            data = json.loads(request.body) if request.body else {}
            mac_address = data.get("mac_address")
        except Exception:
            pass

        if not mac_address:
            mac_address = config.mac_address

        if not mac_address:
            return JsonResponse(
                {
                    "status": "error",
                    "message": "No MAC address specified in form or settings.",
                },
                status=400,
            )

        async with _get_client_lock():
            diag_clients = _get_diag_clients()
            if mac_address in diag_clients:
                client = diag_clients[mac_address]
                if client.is_connected:
                    return JsonResponse(
                        {
                            "status": "success",
                            "message": f"Already connected to {mac_address}.",
                        }
                    )
                else:
                    diag_clients.pop(mac_address)

            detailed_debug = request.GET.get("debug") == "1"
            device_obj = await _find_device_robust(
                mac_address, detailed_debug=detailed_debug
            )
            if not device_obj:
                # One last attempt: direct address connect anyway
                if detailed_debug:
                    logger.info(
                        f"[DEBUG] Device not seen in scan. "
                        f"Forcing direct connect to {mac_address}..."
                    )
                device_obj = mac_address

            client = BleakClient(device_obj, timeout=30.0)
            await client.connect()
            diag_clients[mac_address] = client

            await client.write_gatt_char(
                "0000fef1-0000-1000-8000-00805f9b34fb",
                bytes([0x01]),
                response=True,
            )
            return JsonResponse(
                {
                    "status": "success",
                    "message": (
                        f"Connected to {mac_address}. Session active. "
                        "Verified with CMD 01."
                    ),
                }
            )
    except Exception as e:
        detailed_debug = request.GET.get("debug") == "1"
        msg = str(e)
        if detailed_debug:
            import traceback

            msg = f"{msg}\n{traceback.format_exc()}"
        return JsonResponse(
            {"status": "error", "message": f"Connection failed: {msg}"},
            status=400,
        )


async def disconnect_device_view(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid method"}, status=405
        )

    try:
        from asgiref.sync import sync_to_async

        config = await sync_to_async(DeviceConfig.get_solo)()

        mac_address = None
        try:
            data = json.loads(request.body) if request.body else {}
            mac_address = data.get("mac_address")
        except Exception:
            pass

        if not mac_address:
            mac_address = config.mac_address

        async with _get_client_lock():
            diag_clients = _get_diag_clients()

            # If explicit mac provided, disconnect that
            if mac_address and mac_address in diag_clients:
                client = diag_clients.pop(mac_address)
                if client.is_connected:
                    await client.disconnect()

            # Also cleanup empty/dangling if no mac provided
            if not mac_address:
                for addr in list(diag_clients.keys()):
                    client = diag_clients.pop(addr)
                    if client.is_connected:
                        await client.disconnect()

        return JsonResponse(
            {
                "status": "success",
                "message": f"Disconnected session for {mac_address or 'all devices'}.",
            }
        )
    except Exception as e:
        return JsonResponse(
            {"status": "error", "message": f"Disconnect failed: {str(e)}"},
            status=400,
        )


def bt_reset_view(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid method"},
            status=405,
        )
    try:
        subprocess.run(
            ["bluetoothctl", "power", "off"],
            capture_output=True,
            timeout=5,
        )
        _time.sleep(5)
        subprocess.run(
            ["bluetoothctl", "power", "on"],
            capture_output=True,
            timeout=5,
        )
        return JsonResponse(
            {"status": "success", "message": "Bluetooth adapter restarted."}
        )
    except Exception as e:
        return JsonResponse(
            {"status": "error", "message": f"BT reset failed: {e}"},
            status=500,
        )


def generate_calendar_view(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid request method"},
            status=405,
        )

    config = DeviceConfig.get_solo()
    if not config.ical_url:
        messages.error(
            request,
            "No iCal URL configured. Set it in Settings first.",
        )
        return redirect("index")

    try:
        img = generate_calendar_image(config.ical_url)
    except Exception as e:
        messages.error(request, f"Failed to generate calendar image: {e}")
        return redirect("index")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    fname = f"calendar_{int(_time.time())}.png"
    epaper_img = EpaperImage()
    epaper_img.image.save(fname, ContentFile(buf.read()), save=True)

    messages.success(request, "Calendar image generated and added to gallery.")
    return redirect("index")


def generate_prompt_view(request):
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Invalid request method"},
            status=405,
        )

    prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        messages.error(request, "Prompt cannot be empty.")
        return redirect("index")

    config = DeviceConfig.get_solo()
    try:
        img = generate_ai_image(
            prompt,
            api_key=config.pollinations_api_key,
            model=config.pollinations_model,
        )

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        fname = f"prompt_{int(_time.time())}.png"
        epaper_img = EpaperImage()
        epaper_img.image.save(fname, ContentFile(buf.read()), save=True)

        messages.success(
            request, f"Image for prompt '{prompt}' generated successfully."
        )
    except Exception as e:
        messages.error(request, f"Failed to generate prompt image: {e}")
        request.session["last_failed_prompt"] = prompt

    return redirect("index")


def automation_status_view(request):
    """API for fetching current automation state and next update time."""
    from .models import DeviceConfig
    from .calendar import fetch_events_today
    from datetime import datetime
    from dateutil import tz as dateutil_tz

    config = DeviceConfig.get_solo()
    if not config.automation_enabled or not config.ical_url:
        return JsonResponse({"state_str": "DISABLED", "next_str": ""})

    try:
        local_tz = dateutil_tz.tzlocal()
        now = datetime.now(tz=local_tz)
        events = fetch_events_today(config.ical_url, local_tz=local_tz)

        timed_events = [ev for ev in events if not ev["all_day"]]
        timed_events.sort(key=lambda x: x["start"])

        is_busy = False
        busy_event = None
        next_event = None
        from datetime import timedelta
        for ev in timed_events:
            start_threshold = ev["start"] - timedelta(minutes=2)
            if start_threshold <= now <= ev["end"]:
                is_busy = True
                busy_event = ev
            elif start_threshold > now:
                if next_event is None or start_threshold < (next_event["start"] - timedelta(minutes=2)):
                    next_event = ev

        state_str = f"[{'BUSY' if is_busy else 'FREE'}]"
        if is_busy and busy_event:
            state_str += f" - {busy_event['summary']}"

        next_str = ""
        if is_busy and busy_event:
            next_str = f"Next change at: {busy_event['end'].strftime('%H:%M')}"
        elif next_event:
            next_time = next_event['start'] - timedelta(minutes=2)
            next_str = f"Next event at: {next_time.strftime('%H:%M')}"

        last_str = ""
        if config.last_automation_time:
            last_str = f"Last update: {config.last_automation_time.strftime('%H:%M')}"

        return JsonResponse(
            {
                "state_str": state_str,
                "next_str": next_str,
                "last_str": last_str,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
