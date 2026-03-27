import asyncio
import logging
from datetime import datetime
from dateutil import tz as dateutil_tz
from .models import DeviceConfig
from .calendar import fetch_events_today
from .ble_logic import run_with_cleanup

logger = logging.getLogger(__name__)


class DummyQueue:
    """A minimal queue-like object for automation tasks."""

    def put(self, item):
        if item:
            logger.info(f"[Automation] {item}")

    def get(self):
        return None


def check_and_update_automation():
    """Sync iCal status and update the e-paper if needed."""
    config = DeviceConfig.get_solo()
    if not config.automation_enabled or not config.ical_url:
        return

    try:
        local_tz = dateutil_tz.tzlocal()
        now = datetime.now(tz=local_tz)
        events = fetch_events_today(config.ical_url, local_tz=local_tz)

        is_busy = False
        busy_event = None
        for ev in events:
            if not ev["all_day"] and ev["start"] <= now <= ev["end"]:
                is_busy = True
                busy_event = ev
                break

        target_image = (
            config.ical_busy_image if is_busy else config.ical_free_image
        )

        if not target_image:
            status = 'busy' if is_busy else 'free'
            logger.warning(
                f"No {status} image configured for automation."
            )
            return

        if config.last_automation_image == target_image:
            # Already set, skip update
            return

        logger.info(
            f"Automation: status {'BUSY' if is_busy else 'FREE'}. "
            f"New target image: {target_image}"
        )
        if is_busy and busy_event:
            logger.info(f"Active event: {busy_event['summary']}")

        # Trigger update
        msg_queue = DummyQueue()
        gicisky_logger = logging.getLogger("gicisky_tag")
        handler = logging.StreamHandler()

        # We need to run the async portion synchronously here or use a thread
        asyncio.run(run_with_cleanup(
            target_image.id, msg_queue, gicisky_logger, handler
        ))

        # Update tracking
        config.last_automation_image = target_image
        config.save()
        logger.info("Automation: Update successful.")

    except Exception as e:
        logger.error(f"Automation error: {e}", exc_info=True)
