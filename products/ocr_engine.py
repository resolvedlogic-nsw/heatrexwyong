import os
import re
from google.cloud import vision
from django.conf import settings

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(settings.BASE_DIR, "vision_key.json")

# --- THE NEW FORMATTING MACHINE ---
def format_r_number(raw_string):
    """Takes any messy string (214, R4862, 10255A) and forces it to standard R0000 format."""
    if not raw_string: 
        return ""
        
    clean_str = str(raw_string).strip().upper()
    
    # Look for digits, followed by any optional letters (ignoring any existing 'R')
    match = re.search(r'(\d+)([A-Z]*)', clean_str)
    
    if match:
        numbers = match.group(1).zfill(4) # This adds the '0' if it's only 3 digits!
        suffix = match.group(2)
        return f"R{numbers}{suffix}"
        
    return clean_str # Fallback just in case

# --- THE EXTRACTION ENGINE ---
def extract_card_data(image_path):
    client = vision.ImageAnnotatorClient()
    with open(image_path, "rb") as image_file:
        content = image_file.read()

    image = vision.Image(content=content)
    response = client.document_text_detection(image=image)
    
    if response.error.message:
        return {'r_number': '', 'voltage': '', 'wattage': '', 'dimensions': ''}

    text = response.full_text_annotation.text
    data = {'r_number': '', 'voltage': '', 'wattage': '', 'dimensions': ''}
    
    # 1. Hunt for the R-Number
    # First, try finding an explicit R followed by numbers
    explicit_r = re.search(r'\bR(\d{3,5}[A-Z]?)\b', text)
    if explicit_r:
        data['r_number'] = format_r_number(explicit_r.group(1))
    else:
        # Fallback: Look for a naked 4 or 5 digit number. 
        # (We skip 3-digit numbers here so the AI doesn't accidentally grab "240" volts as the R-Number)
        naked_num = re.search(r'\b(\d{4,5}[A-Z]?)\b', text)
        if naked_num:
            data['r_number'] = format_r_number(naked_num.group(1))

    # The rest of the parser remains the same
    volt_match = re.search(r'Voltage:.*?(\d{3})', text, re.IGNORECASE | re.DOTALL)
    if volt_match: data['voltage'] = volt_match.group(1)
        
    watt_match = re.search(r'Wattage:\s*(\d{2,5})', text, re.IGNORECASE)
    if watt_match: data['wattage'] = watt_match.group(1)

    dim_matches = re.findall(r'(\d+)\s*[xX×]\s*(\d+)', text)
    if dim_matches:
        dims = [f"{m[0]}x{m[1]}" for m in dim_matches[:2]]
        data['dimensions'] = " / ".join(dims)

    return data