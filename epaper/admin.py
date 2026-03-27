from django.contrib import admin

from .models import EpaperImage, DeviceConfig


@admin.register(EpaperImage)
class EpaperImageAdmin(admin.ModelAdmin):
    list_display = ("id", "text_overlay", "uploaded_at")
    list_filter = ("uploaded_at",)


@admin.register(DeviceConfig)
class DeviceConfigAdmin(admin.ModelAdmin):
    list_display = (
        "mac_address",
        "raw_type",
        "rotate",
        "negative",
        "dithering",
    )
    list_filter = ("rotate", "negative", "dithering")
    search_fields = ("mac_address",)
