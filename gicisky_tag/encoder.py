import math
from enum import Enum
from PIL import Image
import numpy as np
from os import path
from gicisky_tag.log import logger

black_color = [0, 0, 0]  # [47, 36, 41]
white_color = [255, 255, 255]  # [242, 244, 239]
red_color = [255, 0, 0]  # [215, 38, 39]
gray_color = [128, 128, 128]
blue_color = [0, 0, 255]
ciano_color = [0, 255, 255]
green_color = [0, 255, 0]
yellow_color = [255, 255, 0]
magenta_color = [255, 0, 255]


def quantize_image_simple_colors(image, debug_folder=None):
    """Quantize the image to simple colors."""
    quant_palette_image = Image.new("P", (1, 1))
    quant_palette_image.putpalette(
        black_color
        + white_color
        + red_color
        + blue_color
        + green_color
        + yellow_color
        + magenta_color
        + ciano_color
        + gray_color
    )
    quant_image = image.convert("RGB").quantize(
        palette=quant_palette_image,
        dither=Image.FLOYDSTEINBERG,
    )

    if debug_folder is not None:
        quant_image.save(path.join(debug_folder, "simple_image.png"))

    return quant_image


class Dither(Enum):
    """Dithering method.

    Possible values:
    * NONE: no dithering, just choose the closest color for each pixel.
    * FLOYDSTEINBERG: quantize the image using Floyd-Steinberg dithering.
    * COMBINED: quantize grayscale and red colors independently using Floyd-Steinberg dithering,
        then combine them. This usually limits the usage of red to the areas where it is really
        needed.
    """

    NONE = "none"
    FLOYDSTEINBERG = "floydsteinberg"
    COMBINED = "combined"

    def __str__(self):
        return self.value


def dither_image_bwr(image, dithering: Dither, debug_folder=None):
    """Dither the image using black, white and red."""
    if not isinstance(dithering, Dither):
        raise ValueError(f"Invalid dithering parameter: {dithering}")

    if dithering in (Dither.NONE, Dither.FLOYDSTEINBERG):
        bwr_palette_image = Image.new("P", (1, 1))
        bwr_palette_image.putpalette(black_color + white_color + red_color)
        quant_image = image.convert("RGB").quantize(
            palette=bwr_palette_image,
            dither=(
                Image.NONE
                if dithering == Dither.NONE
                else Image.FLOYDSTEINBERG
            ),
        )

        if debug_folder is not None:
            quant_image.save(path.join(debug_folder, "quant_image.png"))

        return quant_image

    elif dithering == Dither.COMBINED:
        quant_image = quantize_image_simple_colors(
            image, debug_folder=debug_folder
        ).convert("RGB")

        bw_image = image.convert("1")
        bw_bitmap = np.asarray(bw_image).astype(bool)
        assert (
            bw_bitmap.shape == image.size[::-1]
        ), f"Expected shape {image.size[::-1]}, but got {bw_bitmap.shape}"

        red_bitmap = (np.asarray(quant_image) == red_color).all(axis=-1)
        assert (
            red_bitmap.shape == image.size[::-1]
        ), f"Expected shape {image.size[::-1]}, but got {red_bitmap.shape}"

        bwr_pixels = np.zeros((*image.size[::-1], 3), dtype=np.uint8)
        bwr_pixels[red_bitmap] = red_color
        bwr_pixels[~red_bitmap & bw_bitmap] = white_color
        bwr_pixels[~red_bitmap & ~bw_bitmap] = black_color
        bwr_image = Image.fromarray(bwr_pixels, "RGB")

        if debug_folder is not None:
            bw_image.save(path.join(debug_folder, "bw_image.png"))
            Image.fromarray(np.uint8(bw_bitmap * 255), "L").save(
                path.join(debug_folder, "bw_bitmap.png")
            )
            Image.fromarray(np.uint8(red_bitmap * 255), "L").save(
                path.join(debug_folder, "red_bitmap.png")
            )
            bwr_image.save(path.join(debug_folder, "bwr_image.png"))

        return bwr_image


class ColorType(Enum):
    BW = 0
    BWR = 1
    BWY = 2
    BWRY = 3
    BWRGBYO = 4


class TagModel:
    def __init__(self, raw_type=None):
        if raw_type is None:
            # Default to 250x122 BWR, with compression (common case)
            self.width = 250
            self.height = 122
            self.color_type = ColorType.BWR
            self.use_compression = True
            self.mirror_image = False
            return

        # rawType = (data.getUint8(4) << 8) | data.getUint8(0);
        screen_resolution = (raw_type >> 5) & 63
        self.display_type = (
            raw_type >> 3
        ) & 3  # 0: TFT, 1: EPA, 2: EPA1, 3: EPA2
        self.color_type = ColorType(
            ((raw_type >> 1) & 3) + ((raw_type >> 10) & 12)
        )
        self.use_compression = (raw_type & 0x4000) == 0
        self.mirror_image = self.display_type in (0, 1, 3)

        # Mapping from HTML:
        # case 0: 104x212
        # case 1: 128x296
        # case 2: 400x300
        # case 3: 384x640
        # case 4: 640x960
        # case 5: 132x250
        # case 6: 96x196
        # case 7: 480x640
        # case 8: 128x250
        # case 9: 800x480
        # case 10: 480x280

        # NOTE: The HTML seems to have width and height swapped compared to what we expect
        # for a horizontal display. We use (width, height) as in PIL.
        # But wait, the HTML says width=104, height=212 for case 0.
        # Let's use what the HTML says.
        res_map = {
            0: (212, 104),
            1: (296, 128),
            2: (400, 300),
            3: (640, 384),
            4: (960, 640),
            5: (250, 132),
            6: (196, 96),
            7: (640, 480),
            8: (250, 122),
            9: (800, 480),
            10: (480, 280),
        }

        if screen_resolution in res_map:
            self.width, self.height = res_map[screen_resolution]
        else:
            # Fallback for type 2 if it's the 2.1" tag
            # In HTML case 2 is 400x300.
            # But the current code said 250x122.
            # Let's check the raw_type bits again.
            # If screen_resolution is something else...
            logger.warning(
                f"Unknown screen resolution: {screen_resolution}. Defaulting to 250x122."
            )
            self.width, self.height = 250, 122

    def __str__(self):
        return f"TagModel({self.width}x{self.height}, color={self.color_type.name}, compression={self.use_compression})"


def encode_image(
    image, tag_model=None, dithering=Dither.NONE, debug_folder=None
):
    if tag_model is None:
        tag_model = TagModel()

    logger.info(f"Encoding image for {tag_model}...")

    # Resize image to match tag resolution
    image = image.convert("RGB").resize(
        (tag_model.width, tag_model.height), Image.Resampling.LANCZOS
    )

    if tag_model.mirror_image:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)

    bwr_image = dither_image_bwr(
        image, dithering=dithering, debug_folder=debug_folder
    )
    bwr_pixels = np.asarray(bwr_image.convert("RGB")).astype(int)

    # BW bitmap: From ATC1441: White is 1, Black and Red are 0
    # luminance > 128 sets the bit for White.
    bw_bitmap = (bwr_pixels == white_color).all(axis=-1)

    # Red bitmap: From ATC1441: Red is 1
    # Red color sets the bit in the second color channel.
    red_bitmap = (bwr_pixels == red_color).all(axis=-1)

    # CRITICAL: The ATC1441 reference reads pixels as:
    #   for i in range(width):       # "column" index
    #       for x in range(height):  # pixel within "column"
    #           curr = (i * height + x)  # sequential chunk index in flat buffer
    #
    # This is NOT a true column-major image read. It treats the flat row-major
    # pixel buffer as if it were laid out (width, height) - i.e., it reads
    # sequential chunks of `height` pixels from the flat array.
    #
    # The numpy equivalent: flatten() then reshape to (width, height).
    # Using .T would give actual image columns (col_i, all_rows), which is WRONG.

    bw_packed = np.packbits(
        bw_bitmap.flatten().reshape(tag_model.width, tag_model.height), axis=-1
    )
    red_packed = np.packbits(
        red_bitmap.flatten().reshape(tag_model.width, tag_model.height),
        axis=-1,
    )

    if tag_model.use_compression:
        bw_data = compress_bitmap_generic(
            bw_packed, tag_model.width, tag_model.height
        )
        red_data = compress_bitmap_generic(
            red_packed, tag_model.width, tag_model.height
        )
    else:
        bw_data = list(bw_packed.flatten())
        red_data = list(red_packed.flatten())

    if tag_model.color_type == ColorType.BW:
        image_data = bytearray(bw_data)
    else:
        # BWR, BWY, BWRY, BWRGBYO
        image_data = bytearray(bw_data) + bytearray(red_data)

    # Gicisky tags expect a 4-byte little-endian length at the start of the data stream,
    # but ONLY when compression is enabled.
    if tag_model.use_compression:
        # The length field must include its own 4 bytes in the total count
        image_data = (len(image_data) + 4).to_bytes(4, "little") + image_data

    return image_data


def compress_bitmap_generic(packed_bitmap, width, height):
    num_line_bytes = math.ceil(height / 8)
    compression_markers = [0x00, 0x00, 0x00, 0x00]
    encoded_bitmap = []

    # packed_bitmap should have shape (width, num_line_bytes)
    for col in range(width):
        line_data = list(packed_bitmap[col])
        encoded_line = (
            [
                0x75,
                3 + len(compression_markers) + len(line_data),
                num_line_bytes,
            ]
            + compression_markers
            + line_data
        )
        encoded_bitmap += encoded_line
    return encoded_bitmap
