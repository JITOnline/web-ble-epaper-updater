import math
import asyncio
import logging
from bleak import BleakClient
from gicisky_tag.log import logger


class ScreenWriter:
    """
    Class to write an image to a screen device.

    Attrbutes:
    - device: The `BleakClient` instance to which the image will be sent.
    - image: The encoded image data, as a `bytes` object.
    - block_size: The block size for the image transfer, as an `int` or `None` if not yet known.
    - transfer_queue:
        An `asyncio.queues.Queue()` that will contain the data of the next image block to send, or `None` if the
        transfer is complete.
    - notify_handler_results:
        An `asyncio.queues.Queue()` that will contain `None` as soon as a notification is handled correctly, or an
        exception if the handling failed.
    """

    REQUEST_CHARACTERISTIC = "0000fef1-0000-1000-8000-00805f9b34fb"
    IMAGE_CHARACTERISTIC = "0000fef2-0000-1000-8000-00805f9b34fb"

    def __init__(self, device, image):
        logger.debug(f"Image data: {len(image)} bytes")
        assert len(image) > 0
        self.device = device
        self.image = image
        self.block_size = None
        self.transfer_queue = asyncio.Queue()
        self.notify_handler_results = asyncio.Queue()

    async def start_notify(self):
        async def notify_handler_task(sender, data):
            try:
                await self.notify_handler(sender, data)
            # Here we catch all exceptions to avoid "Task exception was never retrieved" errors
            except Exception as e:
                logger.error(f"Error in the notify handler: {e}")
                await self.notify_handler_results.put(e)
            else:
                # Signal that the notification was handled correctly
                await self.notify_handler_results.put(None)

        await self.device.start_notify(
            ScreenWriter.REQUEST_CHARACTERISTIC, notify_handler_task
        )

    async def stop_notify(self):
        logger.debug(f"Stop notify")
        await self.device.stop_notify(ScreenWriter.REQUEST_CHARACTERISTIC)

    async def _send_request(self, data):
        logger.debug(
            f"Sending request message: {[data[i] for i in range(len(data))]}",
        )
        if not isinstance(data, bytes):
            data = bytes(data)
        await self.device.write_gatt_char(
            ScreenWriter.REQUEST_CHARACTERISTIC,
            data,
            response=True,
        )
        # Wait until we handled the response of the request
        result = await self.notify_handler_results.get()
        # Propagate an exception if we failed to handle the response
        if result is not None:
            raise result

    async def _send_write(self, data):
        logger.debug(
            f"Sending image message: {[data[i] for i in range(len(data))]}",
        )
        assert len(data) <= self.block_size
        await self.device.write_gatt_char(
            ScreenWriter.IMAGE_CHARACTERISTIC,
            data,
            # Gicisky tags prefer Write Without Response for the image characteristic.
            response=False,
        )

    async def request_block_size(self):
        logger.log(logging.NOTSET, "Request: block size")
        await self._send_request([0x01])

    async def request_write_screen(self):
        assert self.block_size is not None and self.block_size > 0
        size = len(self.image)
        logger.debug(f"Request: write screen (size: {size})")
        await self._send_request([0x02, *size.to_bytes(4, "little")])

    async def request_start_transfer(self):
        logger.debug("Request: start transfer")
        await self._send_request([0x03])

    async def handle_transfer(self):
        logger.debug("Handle transfer")
        while True:
            block = await self.transfer_queue.get()
            if block is None:
                return
            await self.send_image_block(block)
            # 20ms gap to let the loop process incoming notifications more reliably
            await asyncio.sleep(0.02)

    async def request_write_cancel(self):
        logger.debug("Request: write cancel")
        await self._send_request([0x04])

    async def request_write_settings(self, settings):
        await self._send_request([0x40, *settings])

    async def request_refresh(self):
        logger.debug("Request: refresh display")
        await self._send_request([0x01])

    async def request_set_address(self, address):
        await self._send_request([0x19, *address[0:6:-1]])

    async def notify_handler(self, _characteristic, data):
        logger.debug(f"Received notify: {[data[i] for i in range(len(data))]}")
        if data[0] == 0x01:
            assert len(data) == 3
            logger.debug(f"Success: block size request")
            self.block_size = int.from_bytes(data[1:], "little")
            logger.debug(f"Received block size: {self.block_size}")
        elif data[0] == 0x02:
            if data[1] == 0x00:
                logger.debug("Success: write screen request")
            else:
                raise Exception(f"Error: write screen {data[1]}")
        elif data[0] == 0x04:
            if data[1] == 0x00:
                logger.debug("Success: update cancel request")
            else:
                raise Exception(f"Error: update cancel {data[1]}")
        elif data[0] == 0x05:
            if data[1] == 0x00:
                next_part = int.from_bytes(data[2:6], "little")
                logger.debug(f"Success: image transfer request Part {next_part}")
                # Push a new block to be sent by `handle_transfer`
                await self.transfer_queue.put(next_part)
            elif data[1] == 0x08:
                logger.debug(f"Success: image transfer request")
                logger.debug(f"Screen write complete")
                # Signal to `handle_transfer` that the transfer is complete
                await self.transfer_queue.put(None)
            else:
                raise Exception(f"Error: image transfer ({data[1]})")
        elif data[0] == 0x19:
            logger.debug(f"Success: set new address request")
        elif data[0] == 0x40:
            logger.debug(f"Success: set remote device setting request")
        elif data[0] == 0x50:
            logger.debug(f"Success: exit remote device setting request")
        else:
            logger.error(f"Unknown state: {data}")

    async def send_image_block(self, part):
        # Resolve safest block size with robust fallback logic
        mtu_limit = (self.device.mtu_size - 7) if (self.device.mtu_size and self.device.mtu_size > 7) else 23
        hw_limit = self.block_size if self.block_size else 128
        safe_block_size = min(mtu_limit, hw_limit, 128)
        
        img_block_size = safe_block_size - 4
        num_parts = math.ceil(len(self.image) / img_block_size)
        assert (
            part < num_parts
        ), f"Part {part} is too high, there are only {num_parts} parts."
        logger.info(f"Sending image part {part + 1}/{num_parts} (Size: {img_block_size})")
        image_block = self.image[
            part * img_block_size : part * img_block_size + img_block_size
        ]
        assert 0 < len(image_block) <= img_block_size
        message = bytearray([*part.to_bytes(4, "little"), *image_block])
        await self._send_write(message)


async def send_data_to_screen(address, image_data):
    logger.info(f"Connecting to {address}...")
    async with BleakClient(address) as device:
        # Give the service discovery and internal stack time to settle
        await asyncio.sleep(1.5)
        logger.debug(f"MTU: {device.mtu_size}")

        screen = ScreenWriter(device, image_data)
        logger.info(f"Sending image data...")
        await screen.start_notify()
        await screen.request_block_size()
        await screen.request_write_screen()
        await screen.request_start_transfer()
        await screen.handle_transfer()
        logger.info("Transfer complete. Triggering refresh...")
        await screen.request_refresh()
        await asyncio.sleep(1.0)  # Wait for display to start refresh before disconnecting
        await screen.stop_notify()
