import requests
import urllib.parse
import logging
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)


def generate_ai_image(prompt, api_key=None, width=800, height=480):
    """
    Generates an image based on a prompt using pollinations.ai.
    If an api_key is provided, it uses the gen.pollinations.ai endpoint
    with a Bearer token.
    Otherwise, it fallbacks to the free image.pollinations.ai endpoint.
    Returns a grayscale PIL Image object of the specified dimensions.
    """
    v_prompt = urllib.parse.quote(prompt)

    if api_key:
        # Use the newer/paid endpoint if api_key is provided
        url = (
            f"https://gen.pollinations.ai/image/{v_prompt}"
            f"?width={width}&height={height}&nologo=true"
        )
        headers = {"Authorization": f"Bearer {api_key}"}
        logger.debug(f"Calling gen.pollinations.ai for prompt: {prompt}")
    else:
        # Fallback to the public endpoint
        url = (
            f"https://image.pollinations.ai/prompt/{v_prompt}"
            f"?width={width}&height={height}&nologo=true"
        )
        headers = {}
        logger.debug(f"Calling image.pollinations.ai for prompt: {prompt}")

    response = requests.get(url, headers=headers, timeout=120)
    response.raise_for_status()

    # Try to determine if we got an image directly or a JSON response
    content_type = response.headers.get("Content-Type", "").lower()

    if "json" in content_type:
        try:
            data = response.json()
            if "url" in data:
                logger.debug(f"API returned JSON with URL: {data['url']}")
                img_response = requests.get(data["url"], timeout=120)
                img_response.raise_for_status()
                return Image.open(BytesIO(img_response.content)).convert("L")
            else:
                # If no URL, check if there's any other indicator
                logger.warning(f"Unexpected JSON response: {data}")
                raise ValueError("API returned JSON without a 'url' field.")
        except (ValueError, KeyError) as e:
            logger.error(f"Failed to parse JSON response from pollinations: {e}")
            raise

    # Otherwise assume it's image bytes directly
    try:
        img = Image.open(BytesIO(response.content)).convert("L")
        return img
    except Exception as e:
        logger.error(f"Failed to open image from response: {e}")
        # Log a snippet of the response if it failed to decode
        try:
            snippet = response.content[:100].decode("utf-8", "ignore")
            logger.debug(f"Response snippet: {snippet}")
        except Exception:
            pass
        raise
