import time
import logging
from django.core.management.base import BaseCommand
from epaper.automation import check_and_update_automation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the iCal free/busy automation loop"

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=300,
            help="Interval in seconds between checks (default: 300)",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Starting automation loop (interval: {interval}s)..."
            )
        )

        while True:
            try:
                check_and_update_automation()
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"Error in automation loop: {e}")
                )

            time.sleep(interval)
