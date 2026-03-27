from django import forms
from .models import EpaperImage, DeviceConfig


class EpaperImageForm(forms.ModelForm):
    class Meta:
        model = EpaperImage
        fields = ["image", "text_overlay"]
        widgets = {
            "image": forms.FileInput(
                attrs={"class": "file-input", "accept": "image/*"}
            ),
            "text_overlay": forms.TextInput(
                attrs={
                    "class": "text-input",
                    "placeholder": "Optional: Draw text instead of an image",
                }
            ),
        }


class DeviceConfigForm(forms.ModelForm):
    class Meta:
        model = DeviceConfig
        exclude = ("last_automation_image",)
        widgets = {
            "mac_address": forms.TextInput(
                attrs={
                    "class": "text-input",
                    "placeholder": "Optional: Auto-scans if empty",
                }
            ),
            "raw_type": forms.TextInput(
                attrs={"class": "text-input", "placeholder": "e.g., 40A0"}
            ),
            "width_override": forms.NumberInput(
                attrs={"class": "text-input", "placeholder": "Width"}
            ),
            "height_override": forms.NumberInput(
                attrs={"class": "text-input", "placeholder": "Height"}
            ),
            "dithering": forms.Select(attrs={"class": "select-input"}),
            "ical_url": forms.URLInput(
                attrs={
                    "class": "text-input",
                    "placeholder": "https://calendar.google.com/...ical",
                }
            ),
            "ical_free_image": forms.Select(attrs={"class": "select-input"}),
            "ical_busy_image": forms.Select(attrs={"class": "select-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "ical_url" in self.fields:
            self.fields["ical_url"].assume_scheme = "https"
        # Sequence numbering: newest first matches the gallery forloop.revindex logic
        all_imgs = list(EpaperImage.objects.all().order_by("uploaded_at"))
        # Create a mapping: ID -> #Number
        numbered_choices = [("", "---------")]
        for i, img in enumerate(all_imgs):
            # i=0 is oldest. revindex for oldest is 1.
            # wait, gallery shows newest first.
            # newest index is count.
            # oldest index is 1.
            num = i + 1
            label = f"#{num} - {img}"
            numbered_choices.append((img.id, label))

        self.fields["ical_free_image"].choices = numbered_choices
        self.fields["ical_busy_image"].choices = numbered_choices
