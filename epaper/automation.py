import os
import sys
import logging
from datetime import datetime
from dateutil import tz as dateutil_tz
from crontab import CronTab
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

        # Filter for non-all-day events
        timed_events = [ev for ev in events if not ev["all_day"]]
        timed_events.sort(key=lambda x: x["start"])

        is_busy = False
        busy_event = None
        next_event = None

        for ev in timed_events:
            if ev["start"] <= now <= ev["end"]:
                is_busy = True
                busy_event = ev
            elif ev["start"] > now:
                if next_event is None or ev["start"] < next_event["start"]:
                    next_event = ev

        target_image = (
            config.ical_busy_image if is_busy else config.ical_free_image
        )

        # Log current state
        state_str = f"STATUS: [{'BUSY' if is_busy else 'FREE'}]"
        if is_busy and busy_event:
            state_str += f" - Event: {busy_event['summary']}"
        
        # Calculate next state time
        if is_busy and busy_event:
            next_change = busy_event["end"]
        elif next_event:
            next_change = next_event["start"]
        else:
            next_change = None

        next_str = ""
        if next_change:
            next_str = f" | Next update: {next_change.strftime('%H:%M')}"
        
        logger.info(f"Automation {state_str}{next_str}")
        
        # Update debug console (using a khusus logger that views can pick up)
        status_logger = logging.getLogger("gicisky_tag.automation")
        status_logger.info(f"{state_str}{next_str}")

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
            f"Automation: Change detected. New target image: {target_image}"
        )

        # Trigger update
        msg_queue = DummyQueue()
        gicisky_logger = logging.getLogger("gicisky_tag")
        handler = logging.StreamHandler()

        import asyncio
        asyncio.run(run_with_cleanup(
            target_image.id, msg_queue, gicisky_logger, handler
        ))

        # Update tracking
        config.last_automation_image = target_image
        config.last_automation_time = now
        config.save()
        logger.info("Automation: Update successful.")

    except Exception as e:
        logger.error(f"Automation error: {e}", exc_info=True)


def set_automation_cron(enabled=True):
    """Enable or disable the crontab entry for automation logic."""
    try:
        cron = CronTab(user=True)
        comment = 'epaper-automation-check'
        
        # Remove existing if any
        cron.remove_all(comment=comment)
        
        if enabled:
            # Get path to manage.py
            cur_file = os.path.abspath(__file__)
            # epaper/automation.py -> epaper -> web-ble-epaper-updater
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(cur_file)))
            manage_py = os.path.join(base_dir, 'manage.py')
            python_bin = sys.executable

            command = f'{python_bin} {manage_py} check_automation'
            job = cron.new(command=command, comment=comment)
            job.minute.every(5)
            cron.write()
            logger.info("Automation cron job enabled (every 5 mins).")
        else:
            cron.write()
            logger.info("Automation cron job disabled.")
    except Exception as e:
        logger.error(f"Failed to manage cron job: {e}")
