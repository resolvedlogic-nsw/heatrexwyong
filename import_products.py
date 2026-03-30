import os
import django
import csv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'heatrex_core.settings')
django.setup()

from products.models import Product, Customer, ProductCategory

CSV_FILE = 'products.csv'  

# --- THE MASTER DICTIONARY ---
# This ensures the "Imported -" bug never happens again.
TAXONOMY = {
    'SCB': 'Sheath Clamp Band', 'SSCB': 'Sectional Sheath Clamp Band',
    'TSCB': 'Tubular Sheath Clamp Band', 'SBCB': 'Sectional Body Clamp Band',
    'STCB': 'Section Tubular Clamp Band', 'TSSCB': 'Tubular Sectional Sheath Clamp Band',
    'SCKB': 'Sectional Ceramic Knuckle Band', 'CKB': 'Ceramic Knuckle Band',
    'ICKB': 'Internal Ceramic Knuckle Band', 'BCB': 'Body Clamp Band',
    'BCY': 'Body Clamp "Y" Type', 'PP': 'Pressure Plate',
    'SPP': 'Special Pressure Plate', 'MPP': 'Mica Pressure Plate',
    'SPRA': 'Special Pressure Right Angle', 'SRAPP': 'Special Right Angle Pressure plate',
    'SQPP': 'Square Pressure Plate', 'RAPP': 'Right Angle Pressure Plate',
    'ISPP': 'Internal Special Pressure Plate', 'TUB': 'Tubular Heater',
    'CART': 'Cartridge Heater', 'SBH': 'Special Band Heater',
    'CIH': 'Ceramic Immersion Heater', 'IBH': 'Internal Band Heater',
    'SSBH': 'Section Special Band Heater', 'SIBH': 'Sectional Inner Band Heater',
    'RING': 'Ring Heater', 'PIN': 'Pin Heater',
    'CBH': 'Conical Band Heater', 'SCBH': 'Sectional Conical Band Heater',
    'SPEC': 'Special', 'SPIR': 'Spiral', 'UND': 'Undefined - Please update',
    'MICA': 'Mica', 'CIRC': 'Circular', 'INT': 'Internal', 'STB': 'Sectional Tubular'
}

def import_products():
    print(f"Reading {CSV_FILE}...")
    
    with open(CSV_FILE, mode='r', encoding='utf-8-sig') as file:
        # Check your CSV headers one last time! 
        # If the CSV header says "Type Short", change 'type_short' below to match it.
        reader = csv.DictReader(file)
        
        created_count = 0
        updated_count = 0
        
        for row in reader:
            r_num = row.get('r_number', '').strip()
            if not r_num:
                continue
                
            # 1. Look up the category code
            cat_code = row.get('type_short', '').strip().upper() or 'UND'
            
            # 2. Get the nice name from our dictionary, or use a default if it's a new code
            nice_name = TAXONOMY.get(cat_code, f"New Category - {cat_code}")
                
            # 3. Create the Category correctly
            category, _ = ProductCategory.objects.update_or_create(
                code=cat_code,
                defaults={'name': nice_name}
            )
            
            defaults = {
                'legacy_r_number': row.get('legacy_r_number', '').strip(),
                'category': category,
                'description_invoice': row.get('description_invoice', '').strip(),
                'description_private': row.get('description_private', '').strip(),
                'voltage': row.get('voltage', '').strip(),
                'wattage': row.get('wattage', '').strip(),
                'die_shape': row.get('die_shape', '').strip(),
                'is_active': True
            }
            
            try:
                product, created = Product.objects.update_or_create(
                    r_number=r_num,
                    defaults=defaults
                )
                
                # Link the primary customer
                c_num = row.get('customer_number', '').strip()
                if c_num:
                    try:
                        cust = Customer.objects.get(customer_number=c_num)
                        product.customers.set([cust])
                    except Customer.DoesNotExist:
                        pass # Silently skip missing ghost customers for now

                if created:
                    created_count += 1
                else:
                    updated_count += 1
                    
            except Exception as e:
                print(f"⚠️ Error on {r_num}: {e}")

    print(f"\n✅ DONE! Created: {created_count} | Updated: {updated_count}")

if __name__ == '__main__':
    import_products()