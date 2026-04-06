from django import forms
from .models import Device


class DeviceRegisterForm(forms.ModelForm):
    class Meta:
        model  = Device
        fields = ['name', 'district', 'latitude', 'longitude', 'location_note', 'firmware_version']
        widgets = {
            'name':             forms.TextInput(attrs={'placeholder': 'e.g. North Tangail Node 1'}),
            'location_note':    forms.TextInput(attrs={'placeholder': 'e.g. Rooftop, near the water tower'}),
            'firmware_version': forms.TextInput(attrs={'placeholder': 'e.g. v1.0.0'}),
            'latitude':         forms.NumberInput(attrs={'step': 'any', 'placeholder': '24.0000'}),
            'longitude':        forms.NumberInput(attrs={'step': 'any', 'placeholder': '89.0000'}),
        }
        labels = {
            'location_note': 'Location description',
        }


class DeviceEditForm(forms.ModelForm):
    class Meta:
        model  = Device
        fields = ['name', 'district', 'latitude', 'longitude', 'location_note', 'firmware_version']
        widgets = DeviceRegisterForm.Meta.widgets
