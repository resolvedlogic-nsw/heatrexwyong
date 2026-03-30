from django import forms
from .models import Customer, Product, MicaComponent, CaseComponent

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            'customer_number', 'company_name', 'is_active',
            'email', 'phone', 'mobile',
            'address_line_1', 'address_line_2', 'suburb', 'state', 'postcode',
            'rep_name', 'rep_email', 'rep_phone', 'notes'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-control'

# ── NEW PRODUCT FORMS ──

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'r_number',
            'is_active',
            'customers',
            'category',
            'plug_type',
            'description_invoice',
            'description_private',
            'voltage',
            'wattage',
            'turns_apart',
            'die_shape'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name not in ['customers', 'is_active']: # Don't apply form-control to checkboxes or multiple-selects
                field.widget.attrs['class'] = 'form-control'

class MicaComponentForm(forms.ModelForm):
    class Meta:
        model = MicaComponent
        # Exclude 'product' (we link it behind the scenes) and 'image' (we use ProductFile now)
        exclude = ['product', 'image']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'

class CaseComponentForm(forms.ModelForm):
    class Meta:
        model = CaseComponent
        exclude = ['product', 'image']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'

class GenerateAccountForm(forms.Form):
    first_name = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. David'})
    )
    last_name = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Atkinson'})
    )
    email = forms.EmailField(
        label="Direct Email",
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'david@example.com'})
    )