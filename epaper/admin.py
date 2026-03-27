from django.contrib import admin

from .models import EpaperConfig, CalendarConfig

@admin.register(EpaperConfig)
class EpaperConfigAdmin(admin.ModelAdmin):
    list_display = ('mac_address', 'raw_type', 'rotate', 'negative', 'dithering')
    list_filter = ('raw_type', 'rotate', 'negative', 'dithering')
    search_fields = ('mac_address',)

@admin.register(CalendarConfig)
class CalendarConfigAdmin(admin.ModelAdmin):
    list_display = ('ical_url', 'last_updated')
    readonly_fields = ('last_updated',)
