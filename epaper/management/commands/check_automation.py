from django.core.management.base import BaseCommand
from epaper.automation import check_and_update_automation

class Command(BaseCommand):
    help = 'One-shot check for iCal free/busy automation'

    def handle(self, *args, **options):
        check_and_update_automation()
        self.stdout.write("Automation check complete.")
