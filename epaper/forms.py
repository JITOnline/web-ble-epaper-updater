from django import forms
from .models import EpaperImage, DeviceConfig


class EpaperImageForm(forms.ModelForm):
    class Meta:
        model = EpaperImage
        fields = ['image', 'text_overlay']
        widgets = {
            'image': forms.FileInput(
                attrs={'class': 'file-input', 'accept': 'image/*'}
            ),
            'text_overlay': forms.TextInput(
                attrs={
                    'class': 'text-input',
                    'placeholder': 'Optional: Draw text instead of an image',
                }
            ),
        }


class DeviceConfigForm(forms.ModelForm):
    class Meta:
        model = DeviceConfig
        fields = '__all__'
        widgets = {
            'mac_address': forms.TextInput(
                attrs={
                    'class': 'text-input',
                    'placeholder': 'Optional: Auto-scans if empty',
                }
            ),
            'raw_type': forms.TextInput(
                attrs={'class': 'text-input', 'placeholder': 'e.g., 40A0'}
            ),
            'width_override': forms.NumberInput(
                attrs={'class': 'text-input', 'placeholder': 'Width'}
            ),
            'height_override': forms.NumberInput(
                attrs={'class': 'text-input', 'placeholder': 'Height'}
            ),
            'dithering': forms.Select(attrs={'class': 'select-input'}),
            'ical_url': forms.URLInput(
                attrs={
                    'class': 'text-input',
                    'placeholder': 'https://calendar.google.com/...ical',
                }
            ),
        }
