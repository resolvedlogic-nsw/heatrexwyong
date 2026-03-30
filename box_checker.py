import os
from PIL import Image, ImageDraw

# Change this to 'new_front' or 'old_front' depending on the card you are looking at
TEMPLATE_TO_TEST = 'new_front' 

FIELD_MAPS = {
    'new_front': {
        'legacy_r_number':   (285,   3,  506,  56),
        'company_name':      (637,   3, 1595,  56),
        'element_type':      (360,  63,  905, 117),
        'voltage':           (1015,  63, 1240, 117),
        'wattage':           (1355, 63, 1595, 117),
        'die_shape':         (430, 120, 1595, 178),
        'pictorial':         (172, 230, 1595, 713),
        'mica_cover_dims':   (325, 770,  660, 810),
        'mica_cover_qty':    (760, 770,  1060, 810),
        'mica_core_dims':    (325, 815,  660, 870),
        'mica_core_qty':     (760, 815,  1060, 870),
        'mica_loading_ohms': (325, 868,  660, 925),
        'mica_wire_type':    (325, 927,  660, 1005),
        'turns_apart':       (850, 920,  1060, 1005),
        'case_inner_dims':   (1255, 770, 1595, 810),
        'case_outer_dims':   (1255, 815, 1595, 870),
        'case_sheath_dims':  (1255, 868, 1595, 925),
        'case_wire_grade':   (1255, 927, 1595, 1005),
    },
    'old_front': {
        'legacy_r_number':   (294,   3,  530,  84), 
        'company_name':      (658,   3, 1597,  80), 
        'element_type':      (358,  88,  960, 150), 
        'voltage':           (1060, 88, 1265, 147), 
        'wattage':           (1375, 88, 1597, 147), 
        'die_shape':         (430, 153, 1597, 213), 
        'pictorial':         (175, 280, 1597, 663), 
        'mica_cover_dims':   (320, 715,  680, 782), 
        'mica_cover_qty':    (802, 715,  1008, 782), 
        'mica_core_dims':    (320, 783,  680, 836), 
        'mica_core_qty':     (802, 783,  1008, 836), 
        'mica_loading_ohms': (320, 843,  680, 893), 
        'mica_wire_type':    (320, 900,  680, 1000), 
        'turns_apart':       (900, 890,  1075, 966), 
        'case_inner_dims':   (1264, 715, 1597, 782), 
        'case_outer_dims':   (1264, 783, 1597, 836), 
        'case_sheath_dims':  (1264, 843, 1597, 893), 
        'case_wire_grade':   (1264, 900, 1597, 966), 
    }
}

def draw_boxes():
    input_image = "/home/heatrexwyong/heatrex/media/scan_inbox/warped/20260329_034225_picf_F.jpg"
    output_image = "/home/heatrexwyong/heatrex/media/scan_inbox/warped/BOX_CHECK.jpg"

    if not os.path.exists(input_image):
        print(f"❌ Could not find {input_image}")
        return

    img = Image.open(input_image).convert("RGB")
    draw = ImageDraw.Draw(img)

    for field_name, box in FIELD_MAPS[TEMPLATE_TO_TEST].items():
        x1, y1, x2, y2 = box
        draw.rectangle([x1, y1, x2, y2], outline="red", width=3)
        
        text_y = y1 - 12 if y1 > 12 else y1 + 5
        draw.text((x1 + 5, text_y), field_name, fill="blue")

    img.save(output_image)
    print(f"✅ Success! Drew {TEMPLATE_TO_TEST} boxes. Open {output_image} to check.")

if __name__ == '__main__':
    draw_boxes()