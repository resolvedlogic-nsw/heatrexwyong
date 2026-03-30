import os
import django
import csv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'heatrex_core.settings')
django.setup()

from products.models import Customer 

CSV_FILE = 'customers.csv'  

def import_customers():
    # Get absolute path to be 100% sure which file we are opening
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, CSV_FILE)
    
    print(f"Opening: {full_path}")
    
    if not os.path.exists(full_path):
        print("❌ ERROR: File not found!")
        return

    with open(full_path, mode='r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for row in reader:
            c_number = row.get('customer_number', '').strip()
            if not c_number:
                continue
                
            defaults = {
                'company_name': row.get('company_name', '').strip(),
                'address_line_1': row.get('address_line_1', '').strip(),
                'suburb': row.get('suburb', '').strip(),
                'state': row.get('state', '').strip().upper()[:3] or 'NSW',      
                'postcode': row.get('postcode', '').strip()[:4] 
            }
            
            # This is the "Force" part - we'll check manually
            customer, created = Customer.objects.update_or_create(
                customer_number=c_number, 
                defaults=defaults         
            )
            
            if created:
                print(f"✅ CREATED: {c_number} - {defaults['company_name']}")
                created_count += 1
            else:
                # Let's see if the name actually changed
                print(f"🔄 MATCHED: {c_number} (Exists in DB)")
                updated_count += 1
                
    print(f"\nDone! Created: {created_count} | Processed: {updated_count}")

if __name__ == '__main__':
    import_customers()