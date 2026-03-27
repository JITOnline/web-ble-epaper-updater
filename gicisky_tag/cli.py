import sys
import asyncio
import argparse
import logging
from PIL import Image
from gicisky_tag.encoder import encode_image, Dither, TagModel
from gicisky_tag.writer import send_data_to_screen
from gicisky_tag.scanner import find_device
from gicisky_tag.log import logger


async def start(args):
    if args.address is None:
        logger.info("Scanning for device...")
        device_info = await find_device()
        address = device_info["address"]
        raw_type = device_info["raw_type"]
    else:
        address = args.address
        raw_type = None  # We don't have it if address is provided manually

    tag_model = TagModel(raw_type)
    logger.info(f"Target tag: {tag_model}")

    logger.info("Loading image...")
    image = Image.open(args.image)
    image_data = encode_image(
        image, tag_model=tag_model, dithering=args.dithering, debug_folder=args.debug_folder
    )

    await send_data_to_screen(address, image_data)

    logger.info("Done.")


def setup_logger(verbose=False):
    formatter = logging.Formatter(fmt="%(name)s (%(levelname)s): %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger(__package__)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(handler)


def parser():
    parser = argparse.ArgumentParser(description="Write an image to a Gicisky tag.")
    parser.add_argument("--image", type=str, help="Image to send.", required=True)
    parser.add_argument(
        "--address",
        type=str,
        help=(
            "Bluetooth address of the Gicisky tag to be updated. "
            "If not provided, the script will scan and use the first Gicisky tag that it can find."
        ),
    )
    parser.add_argument(
        "--dithering",
        type=Dither,
        choices=list(Dither),
        default=Dither.NONE,
        help=f"Dithering method (default: {Dither.NONE}).",
    )
    parser.add_argument(
        "--debug-folder", type=str, help="Folder in which to save debug data."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )
    return parser


def main():
    args = parser().parse_args()
    setup_logger(args.verbose)
    asyncio.run(start(args))


if __name__ == "__main__":
    main()
