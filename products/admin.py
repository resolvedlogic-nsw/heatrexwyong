from django.contrib import admin
from .models import Customer, Product, MicaComponent, CaseComponent, ProductFile, Job, ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active')
    search_fields = ('code', 'name')


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display  = ('customer_number', 'company_name', 'state', 'phone', 'rep_name', 'is_active')
    search_fields = ('customer_number', 'company_name', 'suburb')
    list_filter   = ('state', 'is_active')
    readonly_fields = ('created_at',)


class MicaComponentInline(admin.StackedInline):
    model = MicaComponent
    extra = 0


class CaseComponentInline(admin.StackedInline):
    model = CaseComponent
    extra = 0


class ProductFileInline(admin.TabularInline):
    model   = ProductFile
    extra   = 1
    fields  = ('file_type', 'file', 'label')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    # Updated to use 'category' and our custom 'get_customers' method
    list_display = ('r_number', 'category', 'get_customers', 'is_active')

    # Updated to filter by the new category table
    list_filter = ('category', 'is_active')

    # Updated the search to look through the new Many-to-Many relationship
    search_fields = ('r_number', 'customers__company_name', 'description_invoice')

    # This replaces raw_id_fields and gives you a beautiful dual-box selection UI!
    filter_horizontal = ('customers',)

    # Django needs a custom helper to display Many-to-Many fields as text in the list view
    def get_customers(self, obj):
        return ", ".join([c.company_name for c in obj.customers.all()])
    get_customers.short_description = 'Assigned Customers'


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display  = ('job_number', 'product', 'status', 'order_quantity', 'requested_date',   'purchase_order')
    search_fields = ('job_number', 'product__r_number', 'product__customer__company_name', 'purchase_order')
    list_filter   = ('status',)
    list_editable = ('status',)
    raw_id_fields = ('product',  )


@admin.register(ProductFile)
class ProductFileAdmin(admin.ModelAdmin):
    list_display  = ('product', 'file_type', 'label', 'created_at', 'uploaded_by')
    search_fields = ('product__r_number', 'label')
    list_filter   = ('file_type',)
    raw_id_fields = ('product',)
