import os
import django
import csv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'heatrex_core.settings')
django.setup()

from products.models import Customer 

def repair_data():
    CSV_FILE = 'customers.csv'
    print(f"🌍 Correcting States and Postcodes from {CSV_FILE}...")

    with open(CSV_FILE, mode='r', encoding='utf-8-sig') as file:
        reader = csv.DictReader(file)
        count = 0
        
        for row in reader:
            # We lowercase the keys here just to be 100% bulletproof
            row = {k.lower().strip(): v for k, v in row.items()}
            
            c_num = row.get('customer_number')
            p_code = row.get('postcode') 
            state = row.get('state')    
            
            if c_num:
                # Update the database
                updated = Customer.objects.filter(customer_number=c_num).update(
                    postcode=p_code,
                    state=state.upper() if state else 'NSW'
                )
                if updated:
                    count += 1
                
    print(f"✅ Finished! {count} customers now have their correct geography.")

if __name__ == '__main__':
    repair_data()