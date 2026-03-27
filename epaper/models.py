from django.db import models
from django.core.exceptions import ValidationError


class EpaperImage(models.Model):
    image = models.ImageField(
        upload_to="epaper_images/", null=True, blank=True
    )
    text_overlay = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Text to overlay on a blank canvas instead of uploading an image"
        ),
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image {self.id} - {'Text' if self.text_overlay else 'File'}"


class DeviceConfig(models.Model):
    DITHER_CHOICES = [
        ("none", "None"),
        ("floydsteinberg", "Floyd-Steinberg"),
        ("combined", "Combined"),
    ]

    mac_address = models.CharField(max_length=17, default="", blank=True)
    raw_type = models.CharField(
        max_length=10,
        blank=True,
        help_text="e.g. 410B. Leave empty to autodetect or use defaults",
    )

    # Manual Overrides
    width_override = models.IntegerField(null=True, blank=True)
    height_override = models.IntegerField(null=True, blank=True)

    rotate = models.BooleanField(default=False)
    negative = models.BooleanField(default=False)
    dithering = models.CharField(
        max_length=20, choices=DITHER_CHOICES, default="none"
    )

    # Forced logic from ATC_GICISKY
    force_compression = models.BooleanField(default=True)
    force_second_color = models.BooleanField(default=True)
    force_mirror = models.BooleanField(default=True)

    # iCal Integration and Free/Busy automation
    ical_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="iCal feed URL for calendar image generation",
    )
    ical_free_image = models.ForeignKey(
        EpaperImage,
        related_name="free_configs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Image to show when no meetings are active",
    )
    ical_busy_image = models.ForeignKey(
        EpaperImage,
        related_name="busy_configs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Image to show when in a meeting",
    )
    automation_enabled = models.BooleanField(
        default=False,
        help_text="Enable automatic switching based on iCal status",
    )
    last_automation_image = models.ForeignKey(
        EpaperImage,
        related_name="last_automated",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    last_automation_time = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.pk and DeviceConfig.objects.exists():
            raise ValidationError(
                "There can be only one DeviceConfig instance"
            )
        return super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(id=1)
        return obj

    def __str__(self):
        return f"Device Config ({self.mac_address})"
