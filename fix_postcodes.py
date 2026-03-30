import os
import django
import csv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'heatrex_core.settings')
django.setup()

from products.models import Customer 

CSV_FILE = 'customers.csv'  

def repair_postcodes():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(base_dir, CSV_FILE)
    
    print(f"Rescuing postcodes from: {full_path}")

    with open(full_path, mode='r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        count = 0
        
        for row in reader:
            c_number = row.get('Customer Number', '').strip()
            # We use 'Postcode' with a capital P to match your CSV
            p_code = row.get('Postcode', '').strip() 
            
            if c_number and p_code:
                # Update ONLY the postcode for this specific customer
                updated = Customer.objects.filter(customer_number=c_number).update(postcode=p_code)
                if updated:
                    count += 1
                
    print(f"✅ Successfully restored {count} postcodes!")

if __name__ == '__main__':
    repair_postcodes()