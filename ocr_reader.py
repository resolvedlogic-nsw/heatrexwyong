
import os
import json
import io
from google.cloud import vision

# ⚠️ Point this to your actual Google Cloud JSON key file!
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "vision_key.json"

def detect_text(image_path):
    """Sends the snippet to Google Vision and returns the text."""
    client = vision.ImageAnnotatorClient()

    with io.open(image_path, 'rb') as image_file:
        content = image_file.read()

    image = vision.Image(content=content)
    response = client.document_text_detection(image=image)

    if response.error.message:
        raise Exception(f"Vision API Error: {response.error.message}")

    if response.text_annotations:
        return response.text_annotations[0].description.strip()
    return ""

def sanitize_text(field_name, text):
    """Cleans up common OCR mistakes based on the field type."""
    # The '+' instead of '4' glitch in dimension fields
    if text.startswith('+'):
        text = '4' + text[1:]

    # Fix the missing 'R' in the legacy number
    if field_name == 'legacy_r_number' and not text.upper().startswith('R'):
        text = 'R' + text

    # Clean up the Ohms reading
    if 'ohms' in field_name:
        text = text.lower().replace('oms', 'ohms')

    # Keep only numbers for quantity fields
    if 'qty' in field_name:
        text = ''.join(filter(str.isdigit, text))

    return text.strip()

def process_snippets(folder_name):
    print(f"🚀 Starting OCR for {folder_name}...\n")
    base_dir = f"ocr_snippets/{folder_name}"

    extracted_data = {"front": {}, "rear": {}}

    for side in ["front", "rear"]:
        side_dir = os.path.join(base_dir, side)
        if not os.path.exists(side_dir):
            continue

        for filename in os.listdir(side_dir):
            if filename.endswith(".png"):
                field_name = filename.replace(".png", "")
                file_path = os.path.join(side_dir, filename)

                print(f"👀 Reading {field_name}...")
                raw_text = detect_text(file_path)

                clean_text = raw_text.replace('\n', ' ').strip()
                final_text = sanitize_text(field_name, clean_text)

                extracted_data[side][field_name] = final_text
                print(f"   ✅ {final_text}")

    # Try to get the real R-number. If blank/missing, fall back to folder_name
    real_r_number = extracted_data["front"].get("legacy_r_number", folder_name)
    if not real_r_number:
        real_r_number = folder_name

    extracted_data["r_number"] = real_r_number

    # Save the file using the REAL R-number (e.g., R4863_data.json)
    output_file = f"ocr_snippets/{real_r_number}_data.json"
    with open(output_file, "w") as f:
        json.dump(extracted_data, f, indent=4)

    print(f"\n🎉 OCR Complete! All data saved to {output_file}")

# --- THIS IS THE TRIGGER THAT WAS LIKELY MISSING ---
if __name__ == "__main__":
    process_snippets("R_TEST_001")