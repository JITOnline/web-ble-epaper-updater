"""Tests for the epaper Django application and gicisky_tag library."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock, AsyncMock
from io import BytesIO

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from PIL import Image
import numpy as np

from epaper.models import EpaperImage, DeviceConfig
from epaper.forms import EpaperImageForm, DeviceConfigForm
from epaper.views import (
    _configure_tag_model,
    _prepare_image,
)
from gicisky_tag.encoder import (
    Dither,
    TagModel,
    ColorType,
    encode_image,
    dither_image_bwr,
    compress_bitmap_generic,
)
from gicisky_tag.writer import ScreenWriter


# ── Helper ────────────────────────────────────────────────────────

def _make_png(width=250, height=122, color='white'):
    """Create a minimal in-memory PNG for testing."""
    img = Image.new('RGB', (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════
#  Model tests
# ══════════════════════════════════════════════════════════════════

class EpaperImageModelTest(TestCase):
    def test_create_text_overlay(self):
        obj = EpaperImage.objects.create(text_overlay="hello")
        self.assertEqual(str(obj), f"Image {obj.id} - Text")

    def test_create_with_image(self):
        png = _make_png()
        uploaded = SimpleUploadedFile(
            "test.png", png.read(), content_type="image/png"
        )
        obj = EpaperImage.objects.create(image=uploaded)
        self.assertIn("File", str(obj))
        obj.image.delete(save=False)


class DeviceConfigModelTest(TestCase):
    def test_get_solo_creates_default(self):
        self.assertEqual(DeviceConfig.objects.count(), 0)
        cfg = DeviceConfig.get_solo()
        self.assertEqual(cfg.id, 1)
        self.assertEqual(DeviceConfig.objects.count(), 1)

    def test_get_solo_returns_existing(self):
        DeviceConfig.get_solo()
        cfg2 = DeviceConfig.get_solo()
        self.assertEqual(cfg2.id, 1)
        self.assertEqual(DeviceConfig.objects.count(), 1)

    def test_singleton_enforcement(self):
        DeviceConfig.objects.create(id=1)
        with self.assertRaises(ValidationError):
            DeviceConfig(mac_address="AA:BB:CC:DD:EE:FF").save()

    def test_str(self):
        cfg = DeviceConfig.get_solo()
        self.assertIn("Device Config", str(cfg))


# ══════════════════════════════════════════════════════════════════
#  Form tests
# ══════════════════════════════════════════════════════════════════

class EpaperImageFormTest(TestCase):
    def test_text_only_valid(self):
        form = EpaperImageForm(data={'text_overlay': 'test'})
        self.assertTrue(form.is_valid())

    def test_empty_valid(self):
        form = EpaperImageForm(data={})
        self.assertTrue(form.is_valid())


class DeviceConfigFormTest(TestCase):
    def test_defaults_valid(self):
        form = DeviceConfigForm(data={
            'mac_address': '',
            'raw_type': '',
            'rotate': False,
            'negative': False,
            'dithering': 'none',
            'force_compression': True,
            'force_second_color': True,
            'force_mirror': True,
            'ical_url': '',
        })
        self.assertTrue(form.is_valid())

    def test_invalid_dithering(self):
        form = DeviceConfigForm(data={
            'mac_address': '',
            'raw_type': '',
            'rotate': False,
            'negative': False,
            'dithering': 'invalid_value',
            'force_compression': True,
            'force_second_color': True,
            'force_mirror': True,
            'ical_url': '',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('dithering', form.errors)


# ══════════════════════════════════════════════════════════════════
#  View tests (Django test client)
# ══════════════════════════════════════════════════════════════════

class IndexViewTest(TestCase):
    def test_get_index(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('config_form', resp.context)

    def test_post_config_update(self):
        DeviceConfig.get_solo()
        resp = self.client.post('/', {
            'mac_address': 'AA:BB:CC:DD:EE:FF',
            'raw_type': '',
            'rotate': False,
            'negative': False,
            'dithering': 'none',
            'force_compression': True,
            'force_second_color': True,
            'force_mirror': True,
            'ical_url': '',
        })
        self.assertEqual(resp.status_code, 302)
        cfg = DeviceConfig.objects.get(id=1)
        self.assertEqual(cfg.mac_address, 'AA:BB:CC:DD:EE:FF')


class UploadViewTest(TestCase):
    def test_upload_text(self):
        resp = self.client.post('/upload/', {
            'text_overlay': 'Hello E-Paper',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EpaperImage.objects.count(), 1)

    def test_upload_image(self):
        png = _make_png()
        uploaded = SimpleUploadedFile(
            "test.png", png.read(), content_type="image/png"
        )
        resp = self.client.post('/upload/', {'image': uploaded})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EpaperImage.objects.count(), 1)
        EpaperImage.objects.first().image.delete(save=False)


class DeleteViewTest(TestCase):
    def test_delete_image(self):
        obj = EpaperImage.objects.create(text_overlay="temp")
        resp = self.client.post(f'/delete/{obj.id}/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(EpaperImage.objects.count(), 0)

    def test_delete_nonexistent_returns_404(self):
        resp = self.client.post('/delete/99999/')
        self.assertEqual(resp.status_code, 404)

    def test_get_not_allowed(self):
        obj = EpaperImage.objects.create(text_overlay="temp")
        resp = self.client.get(f'/delete/{obj.id}/')
        self.assertEqual(resp.status_code, 405)


class TriggerViewTest(TestCase):
    def test_get_not_allowed(self):
        obj = EpaperImage.objects.create(text_overlay="test")
        resp = self.client.get(f'/trigger/{obj.id}/')
        self.assertEqual(resp.status_code, 405)


class BtResetViewTest(TestCase):
    @patch('epaper.views.subprocess.run')
    @patch('epaper.views._time.sleep')
    def test_bt_reset_success(self, mock_sleep, mock_run):
        resp = self.client.post('/bt-reset/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(mock_run.call_count, 2)

    def test_get_not_allowed(self):
        resp = self.client.get('/bt-reset/')
        self.assertEqual(resp.status_code, 405)


class CalendarViewTest(TestCase):
    def test_get_not_allowed(self):
        resp = self.client.get('/generate-calendar/')
        self.assertEqual(resp.status_code, 405)

    def test_no_ical_url_redirects(self):
        DeviceConfig.get_solo()
        resp = self.client.post('/generate-calendar/')
        self.assertEqual(resp.status_code, 302)


# ══════════════════════════════════════════════════════════════════
#  View helper tests
# ══════════════════════════════════════════════════════════════════

class ConfigureTagModelTest(TestCase):
    def test_defaults_no_raw_type(self):
        cfg = DeviceConfig.get_solo()
        tag = _configure_tag_model(cfg, None)
        self.assertEqual(tag.width, 250)
        self.assertEqual(tag.height, 122)
        self.assertTrue(tag.use_compression)

    def test_override_dimensions(self):
        cfg = DeviceConfig.get_solo()
        cfg.width_override = 800
        cfg.height_override = 480
        tag = _configure_tag_model(cfg, None)
        self.assertEqual(tag.width, 800)
        self.assertEqual(tag.height, 480)

    def test_force_second_color_bw_to_bwr(self):
        cfg = DeviceConfig.get_solo()
        cfg.force_second_color = True
        tag = _configure_tag_model(cfg, None)
        # Default TagModel is already BWR, so this should stay BWR
        self.assertEqual(tag.color_type, ColorType.BWR)

    def test_disable_second_color(self):
        cfg = DeviceConfig.get_solo()
        cfg.force_second_color = False
        tag = _configure_tag_model(cfg, None)
        self.assertEqual(tag.color_type, ColorType.BW)

    def test_raw_type_hex_override(self):
        cfg = DeviceConfig.get_solo()
        cfg.raw_type = '410B'
        tag = _configure_tag_model(cfg, None)
        # raw_type should be parsed from config.raw_type
        self.assertIsInstance(tag, TagModel)


class PrepareImageTest(TestCase):
    def test_text_overlay_encodes(self):
        cfg = DeviceConfig.get_solo()
        obj = EpaperImage.objects.create(text_overlay="Hello")
        tag = TagModel()
        data = _prepare_image(obj, tag, cfg)
        self.assertIsInstance(data, (bytes, bytearray))
        self.assertGreater(len(data), 0)

    def test_rotate_and_negative(self):
        cfg = DeviceConfig.get_solo()
        cfg.rotate = True
        cfg.negative = True
        obj = EpaperImage.objects.create(text_overlay="Test")
        tag = TagModel()
        data = _prepare_image(obj, tag, cfg)
        self.assertIsInstance(data, (bytes, bytearray))


# ══════════════════════════════════════════════════════════════════
#  Encoder tests
# ══════════════════════════════════════════════════════════════════

class TagModelTest(TestCase):
    def test_default_construction(self):
        tag = TagModel()
        self.assertEqual(tag.width, 250)
        self.assertEqual(tag.height, 122)
        self.assertEqual(tag.color_type, ColorType.BWR)
        self.assertTrue(tag.use_compression)
        self.assertFalse(tag.mirror_image)

    def test_known_raw_type_800x480(self):
        # screen_resolution=9 → 800x480
        # bits: resolution in bits [10:5], display_type [4:3],
        #       color [2:1]
        # raw_type where (raw_type >> 5) & 63 == 9
        raw = 9 << 5  # screen_resolution=9, rest=0
        raw |= 0b010  # color_type bits → BWR (value 1)
        tag = TagModel(raw)
        self.assertEqual(tag.width, 800)
        self.assertEqual(tag.height, 480)

    def test_unknown_resolution_defaults(self):
        # screen_resolution=63 → unknown → fallback
        raw = 63 << 5
        tag = TagModel(raw)
        self.assertEqual(tag.width, 250)
        self.assertEqual(tag.height, 122)

    def test_str(self):
        tag = TagModel()
        s = str(tag)
        self.assertIn("250x122", s)
        self.assertIn("BWR", s)


class DitherTest(TestCase):
    def test_enum_values(self):
        self.assertEqual(Dither.NONE.value, "none")
        self.assertEqual(Dither.FLOYDSTEINBERG.value, "floydsteinberg")
        self.assertEqual(Dither.COMBINED.value, "combined")

    def test_str(self):
        self.assertEqual(str(Dither.NONE), "none")


class EncodeImageTest(TestCase):
    def test_encode_white_bwr_compressed(self):
        img = Image.new("RGB", (250, 122), (255, 255, 255))
        tag = TagModel()
        data = encode_image(img, tag_model=tag, dithering=Dither.NONE)
        self.assertIsInstance(data, (bytes, bytearray))
        # Compressed data starts with 4-byte LE length
        length = int.from_bytes(data[:4], "little")
        self.assertEqual(length, len(data))

    def test_encode_bw_mode(self):
        img = Image.new("RGB", (250, 122), (0, 0, 0))
        tag = TagModel()
        tag.color_type = ColorType.BW
        data = encode_image(img, tag_model=tag, dithering=Dither.NONE)
        self.assertIsInstance(data, (bytes, bytearray))

    def test_encode_uncompressed(self):
        img = Image.new("RGB", (250, 122), (255, 255, 255))
        tag = TagModel()
        tag.use_compression = False
        data = encode_image(img, tag_model=tag, dithering=Dither.NONE)
        # No 4-byte length prefix when uncompressed
        self.assertIsInstance(data, (bytes, bytearray))

    def test_encode_floydsteinberg(self):
        img = Image.new("RGB", (250, 122), (128, 64, 64))
        tag = TagModel()
        data = encode_image(
            img, tag_model=tag, dithering=Dither.FLOYDSTEINBERG
        )
        self.assertGreater(len(data), 0)

    def test_encode_combined_dithering(self):
        img = Image.new("RGB", (250, 122), (200, 50, 50))
        tag = TagModel()
        data = encode_image(
            img, tag_model=tag, dithering=Dither.COMBINED
        )
        self.assertGreater(len(data), 0)

    def test_encode_with_mirror(self):
        img = Image.new("RGB", (250, 122), (255, 255, 255))
        tag = TagModel()
        tag.mirror_image = True
        data = encode_image(img, tag_model=tag)
        self.assertGreater(len(data), 0)


class DitherImageBWRTest(TestCase):
    def test_invalid_dithering_raises(self):
        img = Image.new("RGB", (10, 10), (128, 128, 128))
        with self.assertRaises(ValueError):
            dither_image_bwr(img, "not_a_dither_enum")

    def test_none_dithering_returns_image(self):
        img = Image.new("RGB", (10, 10), (255, 0, 0))
        result = dither_image_bwr(img, Dither.NONE)
        self.assertIsNotNone(result)

    def test_combined_dithering_returns_image(self):
        img = Image.new("RGB", (10, 10), (255, 0, 0))
        result = dither_image_bwr(img, Dither.COMBINED)
        self.assertIsNotNone(result)


class CompressBitmapTest(TestCase):
    def test_compress_produces_expected_markers(self):
        bitmap = np.zeros((10, 2), dtype=np.uint8)
        result = compress_bitmap_generic(bitmap, 10, 16)
        # Each column produces: [0x75, length, num_line_bytes,
        #                        0x00, 0x00, 0x00, 0x00, ...data]
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0], 0x75)


# ══════════════════════════════════════════════════════════════════
#  Writer tests (unit, no BLE hardware)
# ══════════════════════════════════════════════════════════════════

class ScreenWriterNotifyHandlerTest(TestCase):
    def _make_writer(self, image_data=b'\x00' * 100):
        device = MagicMock()
        return ScreenWriter(device, image_data)

    def test_handle_block_size(self):
        sw = self._make_writer()
        sw._handle_block_size(bytes([0x01, 0xF4, 0x00]))
        self.assertEqual(sw.block_size, 244)

    def test_handle_status_success(self):
        # Should not raise
        ScreenWriter._handle_status(
            bytes([0x02, 0x00]), "write screen request"
        )

    def test_handle_status_error(self):
        with self.assertRaises(Exception) as ctx:
            ScreenWriter._handle_status(
                bytes([0x02, 0x01]), "write screen request"
            )
        self.assertIn("Error", str(ctx.exception))

    def test_handle_block_size_assertion(self):
        sw = self._make_writer()
        with self.assertRaises(AssertionError):
            # Wrong length data
            sw._handle_block_size(bytes([0x01, 0x00]))
# ══════════════════════════════════════════════════════════════════
#  Calendar logic tests
# ══════════════════════════════════════════════════════════════════

class CalendarLogicTest(TestCase):
    def test_y_for_time(self):
        from epaper.calendar import _y_for_time, GRID_TOP, GRID_H, HOUR_START, TOTAL_HOURS
        from datetime import time
        # Start of day
        self.assertEqual(_y_for_time(time(HOUR_START, 0)), GRID_TOP)
        # End of day (roughly, considering float)
        self.assertAlmostEqual(
            _y_for_time(time(HOUR_START + TOTAL_HOURS, 0)),
            GRID_TOP + GRID_H,
            delta=1
        )
        # Middle
        self.assertEqual(_y_for_time(time(14, 0)), 258)

    @patch('epaper.calendar.requests.get')
    @patch('epaper.calendar.recurring_ical_events.of')
    def test_fetch_events_today(self, mock_of, mock_get):
        from epaper.calendar import fetch_events_today
        from datetime import datetime
        # Mock requests.get
        mock_resp = MagicMock()
        mock_resp.text = "BEGIN:VCALENDAR\nEND:VCALENDAR"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # Mock recurring_ical_events
        mock_event = MagicMock()
        mock_event.get.side_effect = lambda k, default=None: {
            "SUMMARY": "Test Event",
            "DTSTART": MagicMock(dt=datetime(2026, 3, 27, 10, 0)),
            "DTEND": MagicMock(dt=datetime(2026, 3, 27, 11, 0)),
        }.get(k, default)
       
        mock_between = MagicMock()
        mock_between.between.return_value = [mock_event]
        mock_of.return_value = mock_between

        events = fetch_events_today("http://example.com/ical")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['summary'], "Test Event")

    @patch('epaper.calendar.fetch_events_today')
    def test_generate_calendar_image(self, mock_fetch):
        from epaper.calendar import generate_calendar_image
        from datetime import datetime
        mock_fetch.return_value = [
            {
                "summary": "Meeting",
                "start": datetime(2026, 3, 27, 10, 0),
                "end": datetime(2026, 3, 27, 11, 0),
                "all_day": False
            },
            {
                "summary": "All Day",
                "start": datetime(2026, 3, 27, 0, 0),
                "end": datetime(2026, 3, 27, 23, 59),
                "all_day": True
            }
        ]
        img = generate_calendar_image("http://example.com/ical")
        self.assertEqual(img.size, (800, 480))
        self.assertEqual(img.mode, 'RGB')

    def test_compute_column_layout(self):
        from epaper.calendar import _compute_column_layout
        from datetime import datetime
        events = [
            {"summary": "A", "start": datetime(2026, 3, 27, 10, 0), "end": datetime(2026, 3, 27, 11, 0)},
            {"summary": "B", "start": datetime(2026, 3, 27, 10, 30), "end": datetime(2026, 3, 27, 11, 30)},
        ]
        layout = _compute_column_layout(events)
        self.assertEqual(len(layout), 2)
        # Event A: col=0, total=2
        # Event B: col=1, total=2
        self.assertEqual(layout[0][0], 0)
        self.assertEqual(layout[0][1], 2)
        self.assertEqual(layout[1][0], 1)
        self.assertEqual(layout[1][1], 2)


# ══════════════════════════════════════════════════════════════════
#  Writer & Network tests
# ══════════════════════════════════════════════════════════════════

class WriterTests(TestCase):
    @patch('gicisky_tag.writer.BleakClient')
    async def test_send_data_to_screen_mocked(self, mock_client):
        # This test requires async context, but standard TestCase isn't async-aware
        # or methods starting with test_ unless specifically set up.
        # But we can test it as a regular unit test if we mock enough.
        pass # Skipping complex async test for now, or use IsolatedAsyncioTestCase

    def test_screen_writer_init(self):
        device = MagicMock()
        img_data = b'xyz'
        sw = ScreenWriter(device, img_data)
        self.assertEqual(sw.device, device)
        self.assertEqual(sw.image, img_data)


# ══════════════════════════════════════════════════════════════════
#  Extended View tests
# ══════════════════════════════════════════════════════════════════

class ExtendedViewTests(TestCase):
    @patch('epaper.views.threading.Thread')
    def test_trigger_update_post_success(self, mock_thread):
        obj = EpaperImage.objects.create(text_overlay="test")
        resp = self.client.post(f'/trigger/{obj.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get('Content-Type'), 'application/x-ndjson')
        self.assertTrue(mock_thread.called)

class AsyncViewTests(TestCase):
    async def test_resolve_device_no_mac(self):
        with patch('epaper.views.find_device') as mock_find:
            from epaper.views import _resolve_device
            import queue
            mock_find.return_value = {"address": "11:22:33:44:55:66", "raw_type": 0x1234}
            cfg = MagicMock(mac_address="")
            q = queue.Queue()
            addr, rtype = await _resolve_device(cfg, q)
            self.assertEqual(addr, "11:22:33:44:55:66")
            self.assertEqual(rtype, 0x1234)

    async def test_connect_device_success(self):
        with patch('epaper.views.BleakClient') as mock_bleak:
            from epaper.views import connect_device_view
            from epaper.models import DeviceConfig
            await DeviceConfig.objects.aget_or_create(id=1)
            await DeviceConfig.objects.filter(id=1).aupdate(mac_address="11:22:33:44:55:66")
           
            # Mock request
            request = MagicMock()
            request.method = 'POST'
           
            # Mock BleakClient
            mock_instance = mock_bleak.return_value
            mock_instance.connect = AsyncMock()
            mock_instance.write_gatt_char = AsyncMock()
           
            resp = await connect_device_view(request)
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertIn(b'Connected', resp.content)

    async def test_send_cmd_success(self):
        with patch('epaper.views.BleakClient') as mock_bleak:
            from epaper.views import send_cmd_view
            from epaper.models import DeviceConfig
            await DeviceConfig.objects.aget_or_create(id=1)
            await DeviceConfig.objects.filter(id=1).aupdate(mac_address="11:22:33:44:55:66")
           
            request = MagicMock()
            request.method = 'POST'
            request.body = b'{"cmd": "010203"}'
           
            mock_instance = AsyncMock()
            mock_instance.__aenter__.return_value = mock_instance
            mock_bleak.return_value = mock_instance
           
            resp = await send_cmd_view(request)
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertIn(b'Successfully sent', resp.content)

    async def test_disconnect_device_no_conn(self):
        from epaper.views import disconnect_device_view
        from epaper.models import DeviceConfig
        await DeviceConfig.objects.aget_or_create(id=1)
       
        request = MagicMock()
        request.method = 'POST'
       
        resp = await disconnect_device_view(request)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIn(b'No active connection', resp.content)


# ══════════════════════════════════════════════════════════════════
#  Direct Writer tests
# ══════════════════════════════════════════════════════════════════

class ScreenWriterDetailTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_device = MagicMock()
        self.mock_device.write_gatt_char = AsyncMock()
        self.mock_device.mtu_size = 247
        self.image_data = b'\x01\x02\x03\x04' * 100 # 400 bytes
        self.sw = ScreenWriter(self.mock_device, self.image_data)
       
        # We need a side effect to put None into notify_handler_results
        # so _send_request doesn't hang.
        async def mock_write(char, data, response=False):
            if char == ScreenWriter.REQUEST_CHARACTERISTIC:
                await self.sw.notify_handler_results.put(None)
        self.mock_device.write_gatt_char.side_effect = mock_write

    async def test_requests(self):
        await self.sw.request_block_size()
        self.mock_device.write_gatt_char.assert_called()
       
        self.sw.block_size = 244
        await self.sw.request_write_screen()
        await self.sw.request_start_transfer()
        await self.sw.request_refresh()
        self.assertEqual(self.mock_device.write_gatt_char.call_count, 4)

    @patch('gicisky_tag.writer.logger')
    async def test_notify_handler_dispatch(self, mock_logger):
        # Test 0x01 (block size)
        await self.sw.notify_handler(None, bytes([0x01, 0xF4, 0x00]))
        self.assertEqual(self.sw.block_size, 244)

        # Test 0x05 success (part request)
        await self.sw.notify_handler(None, bytes([0x05, 0x00, 0x01, 0x00, 0x00, 0x00]))
        part = await self.sw.transfer_queue.get()
        self.assertEqual(part, 1)

        # Test 0x05 complete
        await self.sw.notify_handler(None, bytes([0x05, 0x08]))
        part = await self.sw.transfer_queue.get()
        self.assertIsNone(part)

        # Test status opcodes
        await self.sw.notify_handler(None, bytes([0x02, 0x00])) # success write
        await self.sw.notify_handler(None, bytes([0x19]))
        await self.sw.notify_handler(None, bytes([0x40]))
        await self.sw.notify_handler(None, bytes([0x50]))
        self.assertGreater(mock_logger.debug.call_count, 5)

    async def test_send_image_block(self):
        self.sw.block_size = 244
        # 400 bytes. block_size=244. message_size=240 image data.
        # Part 0: [0:240]
        # Part 1: [240:400]
        await self.sw.send_image_block(0)
        args, kwargs = self.mock_device.write_gatt_char.call_args
        data = args[1]
        self.assertEqual(data[:4], b'\x00\x00\x00\x00') # part 0
        self.assertEqual(len(data), 244) # 4 + 240

        await self.sw.send_image_block(1)
        args, kwargs = self.mock_device.write_gatt_char.call_args
        data = args[1]
        self.assertEqual(data[:4], b'\x01\x00\x00\x00') # part 1
        self.assertEqual(len(data), 164) # 4 + 160

    async def test_handle_transfer(self):
        # Mock handle_transfer which waits for queue
        await self.sw.transfer_queue.put(0)
        await self.sw.transfer_queue.put(None) # stop
       
        # We need to mock send_image_block because it's called inside
        with patch.object(self.sw, 'send_image_block', new_callable=AsyncMock) as mock_send:
            await self.sw.handle_transfer()
            mock_send.assert_called_with(0)
