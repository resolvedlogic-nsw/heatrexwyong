import os
import io
import re
import json
import time
import shutil
import logging
import argparse
import traceback
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
from PIL import Image
from google.cloud import storage

try:
    from google.cloud import vision as gv
    VISION_AVAILABLE = True
except ImportError:
    gv = None
    VISION_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

BASE_DIR       = Path('/home/heatrexwyong/heatrex')
MEDIA_ROOT     = BASE_DIR / 'media'
INBOX_ROOT     = MEDIA_ROOT / 'scan_inbox'
TEMPLATES_DIR  = BASE_DIR / 'products' / 'scan_templates'
VISION_KEY     = BASE_DIR / 'vision_key.json'

RAW_DIR        = INBOX_ROOT / 'raw'
WARPED_DIR     = INBOX_ROOT / 'warped'
OCR_JSON_DIR   = INBOX_ROOT / 'ocr_json'
REVIEW_DIR     = INBOX_ROOT / 'review'
DONE_DIR       = INBOX_ROOT / 'done'

TARGET_SIZE    = (1600, 1009)
MIN_INLIERS    = 25
LOG_FILE       = INBOX_ROOT / 'processor.log'

GS_BUCKET_NAME = 'heatrex-card-archives'

# ─────────────────────────────────────────────────────────────
# DIGITAL ERASER: Add any printed words you want scrubbed from the rear
# ─────────────────────────────────────────────────────────────
REAR_HEADINGS_TO_REMOVE = [
    "COSTS", "PER HOUR", "TOTAL", "LABOUR", "MATERIALS", "DATE", "SIGNATURE",
    "COST PER HOUR", "TOTAL COST"
]

# ─────────────────────────────────────────────────────────────
# FIELD COORDINATE MAPS
# ─────────────────────────────────────────────────────────────
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
    },
    'new_rear': {
        'full_page_text':  (0, 0, 1600, 1009),
        'rear_upper_crop': (0, 0, 1600, 900),
    },
    'old_rear': {
        'full_page_text':  (0, 0, 1600, 1009),
        'rear_upper_crop': (0, 0, 1600, 900),
    },
}

# ─────────────────────────────────────────────────────────────
# LOGGING & FOLDERS
# ─────────────────────────────────────────────────────────────
def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)-8s  %(message)s',
                        handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()])
log = logging.getLogger(__name__)

def ensure_folders():
    for d in [RAW_DIR, WARPED_DIR, OCR_JSON_DIR, REVIEW_DIR, DONE_DIR]:
        d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# ALIGNMENT (ORB)
# ─────────────────────────────────────────────────────────────
_template_cache = {}
def _load_template(name):
    if name not in _template_cache:
        path = TEMPLATES_DIR / f'{name}.jpg'
        pil = Image.open(path).convert('L')
        _template_cache[name] = np.array(pil)
    return _template_cache[name]

def align_image(scan_path):
    pil = Image.open(scan_path).convert('L')
    scan_gray = np.array(pil)
    orb = cv2.ORB_create(nfeatures=4000)
    bf  = cv2.BFMatcher(cv2.NORM_HAMMING)
    kp_scan, desc_scan = orb.detectAndCompute(scan_gray, None)
    
    if desc_scan is None: return None, None, 0

    best_inliers, best_H, best_key = 0, None, None

    for tmpl_key in ['new_front', 'old_front', 'new_rear', 'old_rear']:
        try: tmpl_gray = _load_template(tmpl_key)
        except FileNotFoundError: continue
        kp_tmpl, desc_tmpl = orb.detectAndCompute(tmpl_gray, None)
        if desc_tmpl is None: continue

        matches = bf.knnMatch(desc_scan, desc_tmpl, k=2)
        good = [m for m, n in matches if m.distance < 0.75 * n.distance]
        if len(good) < 10: continue

        src_pts = np.float32([kp_scan[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_tmpl[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        inliers = int(mask.sum()) if mask is not None else 0

        if inliers > best_inliers:
            best_inliers, best_H, best_key = inliers, H, tmpl_key

    if best_H is None or best_inliers < MIN_INLIERS: return None, best_key, best_inliers

    scan_color = cv2.cvtColor(np.array(Image.open(scan_path).convert('RGB')), cv2.COLOR_RGB2BGR)
    warped_bgr = cv2.warpPerspective(scan_color, best_H, TARGET_SIZE)
    warped_pil = Image.fromarray(cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2RGB))
    
    log.info(f"Aligned {scan_path.name} → {best_key} ({best_inliers} inliers)")
    return warped_pil, best_key, best_inliers

# ─────────────────────────────────────────────────────────────
# OCR & SANITISATION
# ─────────────────────────────────────────────────────────────
_vision_client = None
DRY_RUN = False

def _get_vision_client():
    global _vision_client
    if _vision_client is None:
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(VISION_KEY)
        _vision_client = gv.ImageAnnotatorClient()
    return _vision_client

def ocr_crop(pil_image, box):
    if DRY_RUN: return '(dry-run)'
    crop = pil_image.crop(box)
    w, h = crop.size
    if w < 200 or h < 60:
        scale = max(200 / w, 60 / h, 1)
        crop = crop.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format='PNG')
    client = _get_vision_client()
    resp = client.document_text_detection(image=gv.Image(content=buf.getvalue()))
    return resp.text_annotations[0].description.strip() if resp.text_annotations else ''

def sanitise(field, raw):
    if not raw: return ''
    text = re.sub(r'\.+$', '', raw.replace('\n', ' ').strip()).strip()
    if field in ('voltage', 'wattage', 'mica_loading_ohms', 'turns_apart'):
        return re.sub(r'[^\d.]', '', text.split()[0] if text else '').rstrip('.')
    if field in ('mica_cover_qty', 'mica_core_qty'):
        digits = re.sub(r'\D', '', text)
        return str(min(int(digits[0]), 9)) if digits else ''
    if field.endswith('_dims'):
        text = re.sub(r'\s*[xX×\*]\s*', ' x ', text)
        return re.sub(r'[^\dx .]', '', text).strip().rstrip('.')
    if field == 'legacy_r_number': return text.upper().strip()
    return ' '.join(text.split())

def split_dims(dim_string):
    if not dim_string: return {'_w': '', '_h': '', '_d': ''}
    parts = [p.strip() for p in re.split(r'[xX×\s]+', dim_string) if p.strip()]
    return {'_w': parts[0] if len(parts)>0 else '', '_h': parts[1] if len(parts)>1 else '', '_d': parts[2] if len(parts)>2 else ''}

def format_r_number(raw):
    if not raw: return ''
    clean = raw.strip().upper()
    m = re.search(r'R?(\d{3,5})([A-Z]?)', clean)
    return f"R{m.group(1).zfill(4)}{m.group(2)}" if m else clean

def confidence_score(result):
    score, warnings = 100, []
    r_num = result.get('front', {}).get('legacy_r_number', '')
    if not r_num: score -= 40; warnings.append('R-number not detected')
    elif not re.match(r'^R\d{4}', r_num): score -= 20; warnings.append(f'R-number format unusual: {r_num}')
    if not result.get('front', {}).get('voltage'): score -= 10; warnings.append('Voltage missing')
    if not result.get('front', {}).get('wattage'): score -= 10; warnings.append('Wattage missing')
    inliers = result.get('alignment_inliers', 0)
    if inliers < 40: score -= 15; warnings.append(f'Low alignment confidence ({inliers} inliers)')
    return max(score, 0), warnings

# ─────────────────────────────────────────────────────────────
# CORE PROCESSING FUNCTION
# ─────────────────────────────────────────────────────────────
def process_pair(front_path, rear_path):
    pair_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + front_path.stem
    log.info(f"─── Processing pair: {front_path.name}  /  {rear_path.name if rear_path else 'NO REAR'}")

    result = {
        'pair_id': pair_id, 'processed_at': datetime.now().isoformat(),
        'front_original': front_path.name, 'rear_original': rear_path.name if rear_path else None,
        'template_version': None, 'alignment_inliers': 0, 'front': {}, 'rear': {}, 'confidence': 0, 'warnings': []
    }

    warped_front, tmpl_key, inliers = align_image(front_path)
    if warped_front is None:
        result['warnings'].append('Front alignment failed')
        result['confidence'] = 0
        json_path = REVIEW_DIR / f'{pair_id}.json'
        with open(json_path, 'w') as f: json.dump(result, f, indent=2)
        return json_path

    result['template_version'], result['alignment_inliers'] = tmpl_key, inliers
    warped_front_path = WARPED_DIR / f'{pair_id}_F.jpg'
    warped_front.save(str(warped_front_path), quality=92)
    result['warped_front'] = warped_front_path.name

    warped_rear_path = None
    if rear_path:
        rear_tmpl_key = tmpl_key.replace('front', 'rear')
        warped_rear, _, rear_inliers = align_image(rear_path)
        
        # SLANTY REAR FIX: Fallback to raw image, but force it to 1600x1009 so crops still work!
        if warped_rear is None or rear_inliers < 85:
            warped_rear = Image.open(rear_path).convert('RGB')
            warped_rear = warped_rear.resize(TARGET_SIZE, Image.LANCZOS)
            log.info(f"Rear alignment weak ({rear_inliers} inliers). Bypassing warp, resizing to standard.")

        warped_rear_path = WARPED_DIR / f'{pair_id}_R.jpg'
        warped_rear.save(str(warped_rear_path), quality=92)
        result['warped_rear'] = warped_rear_path.name
        result['rear_tmpl_key'] = rear_tmpl_key

    log.info(f"Running OCR on {tmpl_key} fields...")
    field_map = FIELD_MAPS.get(tmpl_key, {})
    
    front_data = {}
    for field_name, box in field_map.items():
        if field_name == 'pictorial': continue
        raw_text = ocr_crop(warped_front, box)
        if field_name.endswith('_dims'):
            parts = split_dims(sanitise(field_name, raw_text))
            base = field_name.replace('_dims', '')
            for suffix, val in parts.items(): front_data[base + suffix] = val
        else:
            front_data[field_name] = sanitise(field_name, raw_text)

    if front_data.get('legacy_r_number'): front_data['legacy_r_number'] = format_r_number(front_data['legacy_r_number'])
    result['front'] = front_data

    # FULL PAGE REAR PROCESSING
    if warped_rear_path and warped_rear_path.exists():
        rear_version = result.get('rear_tmpl_key', 'new_rear')
        rear_field_map = FIELD_MAPS.get(rear_version, FIELD_MAPS['new_rear'])
        warped_rear_pil = Image.open(warped_rear_path).convert('RGB')
        
        # 1. Whole Page OCR & Scrub
        if 'full_page_text' in rear_field_map:
            raw_text = ocr_crop(warped_rear_pil, rear_field_map['full_page_text'])
            # The Digital Eraser
            for heading in REAR_HEADINGS_TO_REMOVE:
                raw_text = re.sub(r'\b' + re.escape(heading) + r'\b', '', raw_text, flags=re.IGNORECASE)
            result['rear']['description_private'] = sanitise('description_private', raw_text)

        # 2. Upper Rear Image Crop
        if 'rear_upper_crop' in rear_field_map:
            r_pic_crop = warped_rear_pil.crop(rear_field_map['rear_upper_crop'])
            r_pic_path = WARPED_DIR / f'{pair_id}_R_PIC.jpg'
            r_pic_crop.save(str(r_pic_path), quality=92)
            result['rear_pictorial_crop'] = r_pic_path.name

    pic_box = field_map.get('pictorial')
    if pic_box:
        pic_crop = warped_front.crop(pic_box)
        pic_path = WARPED_DIR / f'{pair_id}_PIC.jpg'
        pic_crop.save(str(pic_path), quality=92)
        result['pictorial_crop'] = pic_path.name

    score, warnings = confidence_score(result)
    result['confidence'], result['warnings'] = score, warnings

    out_dir = OCR_JSON_DIR if score >= 70 else REVIEW_DIR
    json_path = out_dir / f'{pair_id}.json'
    with open(json_path, 'w') as f: json.dump(result, f, indent=2)

    log.info(f"Confidence: {score}/100")
    return json_path

# ─────────────────────────────────────────────────────────────
# CLOUD WORKER: FETCH & UPLOAD
# ─────────────────────────────────────────────────────────────
def get_gcs_client():
    if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(VISION_KEY)
    return storage.Client()

def fetch_next_pair_from_cloud():
    client, bucket = get_gcs_client(), get_gcs_client().bucket(GS_BUCKET_NAME)
    blobs = sorted([b for b in bucket.list_blobs(prefix='raw/') if b.name.lower().endswith(('.jpg', '.png'))], key=lambda x: x.name)
    if len(blobs) < 2: return None, None, None, None

    front_blob, rear_blob = blobs[0], blobs[1]
    front_local, rear_local = RAW_DIR / front_blob.name.split('/')[-1], RAW_DIR / rear_blob.name.split('/')[-1]

    log.info(f"☁️ Downloading {front_local.name} and {rear_local.name} from Cloud...")
    front_blob.download_to_filename(str(front_local))
    rear_blob.download_to_filename(str(rear_local))
    return front_local, rear_local, front_blob, rear_blob

def upload_to_cloud(local_path, gcs_prefix):
    blob = get_gcs_client().bucket(GS_BUCKET_NAME).blob(f"{gcs_prefix}/{local_path.name}")
    blob.upload_from_filename(str(local_path))

# ─────────────────────────────────────────────────────────────
# BATCH RUNNER
# ─────────────────────────────────────────────────────────────
def run_batch():
    processed, errors = 0, 0
    while True:
        front_local, rear_local, front_blob, rear_blob = fetch_next_pair_from_cloud()
        if not front_local:
            log.info("No more pending pairs in the Cloud 'raw/' folder.")
            break
        try:
            json_path = process_pair(front_local, rear_local)
            if json_path:
                pair_id = json_path.stem
                w_front = WARPED_DIR / f"{pair_id}_F.jpg"
                w_rear = WARPED_DIR / f"{pair_id}_R.jpg"
                f_pic = WARPED_DIR / f"{pair_id}_PIC.jpg"
                r_pic = WARPED_DIR / f"{pair_id}_R_PIC.jpg"

                if w_front.exists(): upload_to_cloud(w_front, 'warped')
                if w_rear.exists(): upload_to_cloud(w_rear, 'warped')
                if f_pic.exists(): upload_to_cloud(f_pic, 'warped')
                if r_pic.exists(): upload_to_cloud(r_pic, 'warped')

                bucket = get_gcs_client().bucket(GS_BUCKET_NAME)
                bucket.rename_blob(front_blob, f"processing/{front_blob.name.split('/')[-1]}")
                bucket.rename_blob(rear_blob, f"processing/{rear_blob.name.split('/')[-1]}")

                for f in [front_local, rear_local, w_front, w_rear, f_pic, r_pic]:
                    if f.exists(): f.unlink()
            processed += 1
        except Exception as e:
            log.error(f"Error processing {front_local.name}: {e}")
            log.error(traceback.format_exc())
            errors += 1
            break
    return processed

def run_watch(interval=10):
    log.info(f"Cloud Watch mode started (polling every {interval}s).")
    while True:
        try: run_batch()
        except KeyboardInterrupt: break
        except Exception as e: log.error(f"Unexpected error: {e}")
        time.sleep(interval)

if __name__ == '__main__':
    setup_logging()
    ensure_folders()
    parser = argparse.ArgumentParser()
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=int, default=10)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if args.dry_run: DRY_RUN = True
    if args.watch: run_watch(args.interval)
    else: run_batch()