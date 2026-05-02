from django.db import models
from django.core.validators import RegexValidator
from django.contrib.auth.models import User

# --- CUSTOMER MODELS ---

class Customer(models.Model):
    STATE_CHOICES = [
        ("NSW", "New South Wales"), ("VIC", "Victoria"), ("QLD", "Queensland"),
        ("ACT", "Australian Capital Territory"), ("TAS", "Tasmania"),
        ("SA", "South Australia"), ("WA", "Western Australia"), ("NT", "Northern Territory"),
    ]

    phone_regex = RegexValidator(
        regex=r"^\+?1?\d{8,15}$",
        message="Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed.",
    )

    customer_number = models.CharField(max_length=50, unique=True, help_text="e.g., C000000")
    company_name    = models.CharField(max_length=255, unique=True)
    email           = models.EmailField(blank=True, null=True)
    phone           = models.CharField(validators=[phone_regex], max_length=17, blank=True, null=True)
    mobile          = models.CharField(validators=[phone_regex], max_length=17, blank=True, null=True)

    address_line_1  = models.CharField(max_length=200, blank=True, null=True)
    address_line_2  = models.CharField(max_length=200, blank=True, null=True)
    suburb          = models.CharField(max_length=100, blank=True, null=True)
    state           = models.CharField(max_length=3, choices=STATE_CHOICES, default="NSW")
    postcode        = models.CharField(max_length=10, blank=True, null=True)

    rep_name        = models.CharField(max_length=100, blank=True, null=True)
    rep_email       = models.EmailField(blank=True, null=True)
    rep_phone       = models.CharField(validators=[phone_regex], max_length=17, blank=True, null=True)

    notes           = models.TextField(blank=True, null=True, help_text="Internal staff notes")
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer_number} - {self.company_name}"

    class Meta:
        ordering = ['customer_number']


# --- PRODUCT HIERARCHY ---

class ProductCategory(models.Model):
    code = models.CharField(max_length=15, unique=True, help_text="e.g. SCB, MICA, TUB")
    name = models.CharField(max_length=100, help_text="e.g. Strip Ceramic Band")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"[{self.code}] {self.name}"

    class Meta:
        verbose_name_plural = "Product Categories"
        ordering = ['name']


class Product(models.Model):
    # Identification
    r_number         = models.CharField(max_length=50, unique=True, help_text="e.g., R1111 or R00001")
    legacy_r_number  = models.CharField(max_length=50, blank=True, null=True, help_text="Old system R-Number")
    customers        = models.ManyToManyField(Customer, related_name='products', blank=True)
    category         = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, null=True)
    
    # Basic Specs
    voltage          = models.CharField(max_length=50, blank=True, null=True)
    wattage          = models.CharField(max_length=50, blank=True, null=True)
    die_shape        = models.CharField(max_length=100, blank=True, null=True, verbose_name="Die Press / Tooling Shape")
    element_type     = models.CharField(max_length=100, blank=True, null=True)
    turns_apart      = models.CharField(max_length=50, blank=True, null=True)
    plug_type        = models.CharField(max_length=100, blank=True, null=True)
    
    # Descriptions & Notes
    description_invoice = models.TextField(help_text="Visible on portal/invoice", blank=True, null=True)
    description_private = models.TextField(help_text="Internal fabrication details", blank=True, null=True)
    private_notes       = models.TextField(blank=True, null=True, help_text="OCR result from rear card notes")
    other_notes         = models.TextField(blank=True, null=True)

    # Image Assets
    front_card_scan   = models.ImageField(upload_to='card_scans/front/', blank=True, null=True)
    rear_card_scan    = models.ImageField(upload_to='card_scans/rear/', blank=True, null=True)
    pictorial_diagram = models.ImageField(upload_to='diagrams/', blank=True, null=True) # Cropped from front scan

    # Tracking
    creation_date    = models.DateField(auto_now_add=True)
    is_active        = models.BooleanField(default=True)

    def __str__(self):
        category_code = self.category.code if self.category else "UND"
        return f"{self.r_number} ({category_code})"

    class Meta:
        ordering = ['r_number']


# --- DETAILED TECHNICAL COMPONENTS ---

class MicaComponent(models.Model):
    product        = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="mica_details")
    
    # Core Specs
    core_dim_h     = models.CharField(max_length=20, blank=True, null=True)
    core_dim_w     = models.CharField(max_length=20, blank=True, null=True)
    core_dim_d     = models.CharField(max_length=20, blank=True, null=True)
    core_quantity  = models.CharField(max_length=20, blank=True, null=True)
    
    # Cover Specs
    cover_dim_h    = models.CharField(max_length=20, blank=True, null=True)
    cover_dim_w    = models.CharField(max_length=20, blank=True, null=True)
    cover_dim_d    = models.CharField(max_length=20, blank=True, null=True)
    cover_quantity = models.CharField(max_length=20, blank=True, null=True)

    # Electrical Specs
    loading_ohms   = models.CharField(max_length=50, blank=True, null=True)
    wire_type      = models.CharField(max_length=100, blank=True, null=True)
    
    def __str__(self):
        return f"MICA Specs for {self.product.r_number}"


class CaseComponent(models.Model):
    product        = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="case_details")

    # Inner Case
    inner_dim_h    = models.CharField(max_length=20, blank=True, null=True)
    inner_dim_w    = models.CharField(max_length=20, blank=True, null=True)
    inner_dim_d    = models.CharField(max_length=20, blank=True, null=True)

    # Outer Case
    outer_dim_h    = models.CharField(max_length=20, blank=True, null=True)
    outer_dim_w    = models.CharField(max_length=20, blank=True, null=True)
    outer_dim_d    = models.CharField(max_length=20, blank=True, null=True)

    # Sheath Dimensions (New from Coordinate Map)
    sheath_dim_h   = models.CharField(max_length=20, blank=True, null=True)
    sheath_dim_w   = models.CharField(max_length=20, blank=True, null=True)
    sheath_dim_d   = models.CharField(max_length=20, blank=True, null=True)

    wire_grade     = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"Case Specs for {self.product.r_number}"


# --- FILES, JOBS & PORTAL ---

class ProductFile(models.Model):
    FILE_TYPE_CHOICES = [
        ("MICA_DRAWING", "Mica Drawing"), ("CASE_DRAWING", "Case Drawing"),
        ("PHOTO", "Photo"), ("SCAN", "Scanned Legacy File"), ("OTHER", "Other"),
    ]
    product     = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="files")
    file_type   = models.CharField(max_length=20, choices=FILE_TYPE_CHOICES, default="OTHER")
    file        = models.FileField(upload_to="product_files/%Y/%m/")
    label       = models.CharField(max_length=100, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-created_at']


class Job(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending Review"), ("CONFIRMED", "Confirmed"),
        ("IN_PROD", "In Production"), ("QC", "Quality Check"),
        ("DISPATCHED", "Dispatched"), ("INVOICED", "Invoiced"),
    ]

    job_number      = models.CharField(max_length=20, unique=True)
    product         = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="jobs")
    ordered_for_customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, related_name='historical_jobs')
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    order_quantity  = models.IntegerField(default=1)
    requested_date  = models.DateField(auto_now_add=True)
    purchase_order  = models.CharField(max_length=100, blank=True, null=True)
    notes           = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-requested_date']


class PortalProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='portal_users')
    is_principal = models.BooleanField(default=False)