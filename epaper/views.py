import asyncio
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.contrib import messages
from PIL import Image, ImageOps, ImageDraw, ImageFont
from .models import EpaperImage, DeviceConfig
from .forms import EpaperImageForm, DeviceConfigForm

# Import from the manually included gicisky_tag library
from gicisky_tag.encoder import encode_image, Dither, TagModel, ColorType
from gicisky_tag.writer import send_data_to_screen
from gicisky_tag.scanner import find_device
from bleak import BleakClient
import logging

class QueueHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
    def emit(self, record):
        msg = self.format(record)
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self.queue.put_nowait, msg)
        except RuntimeError:
            pass

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

async def trigger_update_view(request, image_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)

    detailed_debug = request.GET.get('debug') == '1'
    log_level = logging.DEBUG if detailed_debug else logging.INFO

    queue = asyncio.Queue()
    handler = QueueHandler(queue)
    handler.setLevel(log_level)
    
    gicisky_logger = logging.getLogger("gicisky_tag")
    gicisky_logger.setLevel(log_level)
    gicisky_logger.addHandler(handler)

    async def run_update():
        try:
            image_obj = await EpaperImage.objects.aget(id=image_id)
            config = await DeviceConfig.objects.aget(id=1)
            
            mac_address = config.mac_address
            raw_type = None

            if not mac_address:
                await queue.put("Scanning for nearby E-Paper tags...")
                device_info = await find_device()
                if not device_info:
                    await queue.put("ERROR: No Tag Found nearby. Please specify MAC Address manually if scanning fails.")
                    return
                mac_address = device_info["address"]
                raw_type = device_info["raw_type"]

            if config.raw_type:
                raw_type = int(config.raw_type, 16)

            tag_model = TagModel(raw_type)

            if config.width_override: tag_model.width = config.width_override
            if config.height_override: tag_model.height = config.height_override
            tag_model.mirror_image = config.force_mirror
            tag_model.use_compression = config.force_compression
            
            if config.force_second_color:
                if tag_model.color_type == ColorType.BW:
                    tag_model.color_type = ColorType.BWR
            else:
                tag_model.color_type = ColorType.BW

            if image_obj.image:
                img = Image.open(image_obj.image.path).convert('RGB')
            else:
                img = Image.new('RGB', (tag_model.width, tag_model.height), color='white')
                draw = ImageDraw.Draw(img)
                text = image_obj.text_overlay or "Hello"
                draw.text((10, tag_model.height // 2 - 10), text, fill='black')
                if tag_model.color_type in [ColorType.BWR, ColorType.BWRY, ColorType.BWRGBYO]:
                    draw.text((10, tag_model.height // 2 + 10), text, fill='red')

            if config.rotate:
                img = img.rotate(180, expand=True)
            if config.negative:
                img = ImageOps.invert(img)

            try:
                dither_val = Dither(config.dithering)
            except ValueError:
                dither_val = Dither.NONE
                
            image_data = encode_image(img, tag_model=tag_model, dithering=dither_val)
            
            await queue.put(f"Starting transfer to {mac_address}...")
            await send_data_to_screen(mac_address, image_data)
            
            await queue.put(f"SUCCESS: Image successfully transferred to MAC {mac_address}!")
        except Exception as e:
            import traceback
            traceback.print_exc()
            await queue.put(f"ERROR: {str(e)}")
        finally:
            gicisky_logger.removeHandler(handler)
            await queue.put(None)

    asyncio.create_task(run_update())

    async def event_stream():
        import json
        while True:
            msg = await queue.get()
            if msg is None:
                break
            yield json.dumps({'msg': msg}) + '\n'

    return StreamingHttpResponse(event_stream(), content_type='application/x-ndjson')

async def send_cmd_view(request):
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            cmd_hex = data.get('cmd', '').strip()
            
            config = await DeviceConfig.objects.aget(id=1)
            mac_address = config.mac_address
            if not mac_address:
                device_info = await find_device()
                if not device_info: return JsonResponse({'status': 'error', 'message': 'No device found'}, status=400)
                mac_address = device_info["address"]
                
            cmd_bytes = bytes.fromhex(cmd_hex)
            async with BleakClient(mac_address) as device:
                await device.write_gatt_char("0000fef1-0000-1000-8000-00805f9b34fb", cmd_bytes, response=True)
            return JsonResponse({'status': 'success', 'message': f'Successfully sent {cmd_hex}'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'error'}, status=405)
