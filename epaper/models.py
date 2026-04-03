from django.db import models
from django.core.exceptions import ValidationError


class EpaperImage(models.Model):
    image = models.ImageField(upload_to="epaper_images/", null=True, blank=True)
    text_overlay = models.CharField(
        max_length=255,
        blank=True,
        help_text=("Text to overlay on a blank canvas instead of uploading an image"),
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
    dithering = models.CharField(max_length=20, choices=DITHER_CHOICES, default="none")

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
    pollinations_api_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="API Key for pollinations.ai image generation",
    )
    pollinations_model = models.CharField(
        max_length=50,
        blank=True,
        default="flux",
        choices=[
            ("kontext", "kontext"),
            ("nanobanana", "nanobanana"),
            ("nanobanana-2", "nanobanana-2"),
            ("nanobanana-pro", "nanobanana-pro"),
            ("seedream5", "seedream5"),
            ("seedream", "seedream"),
            ("seedream-pro", "seedream-pro"),
            ("gptimage", "gptimage"),
            ("gptimage-large", "gptimage-large"),
            ("flux", "flux"),
            ("zimage", "zimage"),
            ("veo", "veo"),
            ("seedance", "seedance"),
            ("seedance-pro", "seedance-pro"),
            ("wan", "wan"),
            ("wan-fast", "wan-fast"),
            ("wan-image", "wan-image"),
            ("wan-image-pro", "wan-image-pro"),
            ("qwen-image", "qwen-image"),
            ("grok-imagine", "grok-imagine"),
            ("grok-imagine-pro", "grok-imagine-pro"),
            ("grok-video-pro", "grok-video-pro"),
            ("klein", "klein"),
            ("ltx-2", "ltx-2"),
            ("p-image", "p-image"),
            ("p-image-edit", "p-image-edit"),
            ("p-video", "p-video"),
            ("nova-canvas", "nova-canvas"),
            ("nova-reel", "nova-reel"),
        ],
    )

    def save(self, *args, **kwargs):
        if not self.pk and DeviceConfig.objects.exists():
            raise ValidationError("There can be only one DeviceConfig instance")
        return super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(id=1)
        return obj

    def __str__(self):
        return f"Device Config ({self.mac_address})"
