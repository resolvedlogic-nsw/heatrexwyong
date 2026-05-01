"""
scan_processor.py
=================
Standalone script. Run from the project root inside the virtualenv.

    python scan_processor.py            # process all pairs in GCS raw/
    python scan_processor.py --watch    # poll GCS every 10s continuously

Pipeline per pair:
  1. Download front + rear JPG from gs://heatrex-card-archives/raw/
  2. Align each image to the nearest template (ORB homography)
  3. OCR every field region via Google Vision
  4. Save warped JPGs + JSON to local media/scan_inbox/ folders
  5. Move originals in GCS from raw/ → done/  (NOT deleted)
  6. Delete local raw download copies to save disk

The warped images stay on local disk so Django can serve them to the
review page via /media/scan_inbox/warped/.  They are deleted by the
Django view AFTER Andrew confirms a record.
"""

import os
import io
import re
import json
import time
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
# CONFIGURATION  —  only edit this block
# ─────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).resolve().parent          # project root
MEDIA_ROOT     = BASE_DIR / 'media'
INBOX_ROOT     = MEDIA_ROOT / 'scan_inbox'
TEMPLATES_DIR  = BASE_DIR / 'products' / 'scan_templates'
VISION_KEY     = BASE_DIR / 'vision_key.json'

RAW_DIR        = INBOX_ROOT / 'raw'        # temp download landing
WARPED_DIR     = INBOX_ROOT / 'warped'     # served by Django /media/
OCR_JSON_DIR   = INBOX_ROOT / 'ocr_json'  # confidence >= 70
REVIEW_DIR     = INBOX_ROOT / 'review'    # confidence < 70
LOG_FILE       = INBOX_ROOT / 'processor.log'

GS_BUCKET_NAME = 'heatrex-card-archives'
GS_RAW_PREFIX  = 'raw/'
GS_DONE_PREFIX = 'done/'

TARGET_SIZE    = (1600, 1009)
MIN_INLIERS    = 25
REAR_MIN_INLIERS = 85   # rear cards have fewer features; fall back to resize below this

# Words to strip from the rear page OCR (printed headings, not handwritten notes)
REAR_HEADINGS_TO_REMOVE = [
    "COSTS", "PER HOUR", "TOTAL", "LABOUR", "MATERIALS",
    "DATE", "SIGNATURE", "COST PER HOUR", "TOTAL COST",
]

# ─────────────────────────────────────────────────────────────
# FIELD COORDINATE MAPS  (x1, y1, x2, y2) on 1600×1009 canvas
# ─────────────────────────────────────────────────────────────

FIELD_MAPS = {
    'new_front': {
        'legacy_r_number':   (285,   3,  506,  56),
        'company_name':      (637,   3, 1595,  56),
        'element_type':      (360,  63,  905, 117),
        'voltage':           (1015, 63, 1240, 117),
        'wattage':           (1355, 63, 1595, 117),
        'die_shape':         (430, 120, 1595, 178),
        'pictorial':         (172, 230, 1595, 713),   # image crop only, not OCR'd
        'mica_cover_dims':   (325, 770,  660, 810),
        'mica_cover_qty':    (760, 770, 1060, 810),
        'mica_core_dims':    (325, 815,  660, 870),
        'mica_core_qty':     (760, 815, 1060, 870),
        'mica_loading_ohms': (325, 868,  660, 925),
        'mica_wire_type':    (325, 927,  660, 1005),
        'turns_apart':       (850, 920, 1060, 1005),
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
        'mica_cover_qty':    (802, 715, 1008, 782),
        'mica_core_dims':    (320, 783,  680, 836),
        'mica_core_qty':     (802, 783, 1008, 836),
        'mica_loading_ohms': (320, 843,  680, 893),
        'mica_wire_type':    (320, 900,  680, 1000),
        'turns_apart':       (900, 890, 1075, 966),
        'case_inner_dims':   (1264, 715, 1597, 782),
        'case_outer_dims':   (1264, 783, 1597, 836),
        'case_sheath_dims':  (1264, 843, 1597, 893),
        'case_wire_grade':   (1264, 900, 1597, 966),
    },
    'new_rear': {
        'full_page':         (0, 0, 1600, 1009),
        'pictorial':         (0, 0, 1600, 776),    # upper image region
    },
    'old_rear': {
        'full_page':         (0, 0, 1600, 1009),
        'pictorial':         (0, 0, 1600, 885),
    },
}

# ─────────────────────────────────────────────────────────────
# LOGGING & FOLDER SETUP
# ─────────────────────────────────────────────────────────────

def setup_logging():
    for d in [RAW_DIR, WARPED_DIR, OCR_JSON_DIR, REVIEW_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s  %(levelname)-8s  %(message)s',
        handlers=[
            logging.FileHandler(str(LOG_FILE)),
            logging.StreamHandler(),
        ],
    )

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# ALIGNMENT
# ─────────────────────────────────────────────────────────────

_template_cache = {}

def _load_template(name):
    if name not in _template_cache:
        path = TEMPLATES_DIR / f'{name}.jpg'
        if not path.exists():
            raise FileNotFoundError(f"Template missing: {path}")
        img = Image.open(path).convert('L')
        _template_cache[name] = np.array(img)
    return _template_cache[name]


def align_image(image_path, prefer_rear=False):
    """
    Align a scan to the best-matching template using ORB feature matching.

    Returns: (warped_pil, template_key, inlier_count)
             warped_pil is None if alignment failed.
    """
    pil_gray  = Image.open(image_path).convert('L')
    scan_gray = np.array(pil_gray)

    orb = cv2.ORB_create(nfeatures=4000)
    bf  = cv2.BFMatcher(cv2.NORM_HAMMING)

    kp_scan, desc_scan = orb.detectAndCompute(scan_gray, None)
    if desc_scan is None:
        return None, None, 0

    order = (
        ['new_rear', 'old_rear', 'new_front', 'old_front']
        if prefer_rear else
        ['new_front', 'old_front', 'new_rear', 'old_rear']
    )

    best_inliers, best_H, best_key = 0, None, None

    for tmpl_key in order:
        try:
            tmpl_gray = _load_template(tmpl_key)
        except FileNotFoundError:
            continue

        kp_tmpl, desc_tmpl = orb.detectAndCompute(tmpl_gray, None)
        if desc_tmpl is None:
            continue

        matches = bf.knnMatch(desc_scan, desc_tmpl, k=2)
        good    = [m for m, n in matches if m.distance < 0.75 * n.distance]
        if len(good) < 10:
            continue

        src_pts = np.float32([kp_scan[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_tmpl[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        inliers = int(mask.sum()) if mask is not None else 0

        if inliers > best_inliers:
            best_inliers, best_H, best_key = inliers, H, tmpl_key

    if best_H is None or best_inliers < MIN_INLIERS:
        return None, best_key, best_inliers

    scan_color = cv2.cvtColor(np.array(Image.open(image_path).convert('RGB')), cv2.COLOR_RGB2BGR)
    warped_bgr = cv2.warpPerspective(scan_color, best_H, TARGET_SIZE)
    warped_pil = Image.fromarray(cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2RGB))

    log.info(f"  Aligned {image_path.name} → {best_key} ({best_inliers} inliers)")
    return warped_pil, best_key, best_inliers

# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────

_vision_client = None

def _get_vision_client():
    global _vision_client
    if _vision_client is None:
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(VISION_KEY)
        _vision_client = gv.ImageAnnotatorClient()
    return _vision_client


def ocr_region(warped_pil, box):
    """Crop warped PIL image to box and return raw OCR string."""
    crop = warped_pil.crop(box)
    w, h = crop.size
    # Upscale tiny crops for better Vision API accuracy
    if w < 200 or h < 50:
        scale = max(200 / w, 50 / h, 1.0)
        crop  = crop.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    crop.save(buf, format='PNG')
    content = buf.getvalue()

    client = _get_vision_client()
    image  = gv.Image(content=content)
    resp   = client.document_text_detection(image=image)

    if resp.error.message:
        log.warning(f"Vision API error: {resp.error.message}")
        return ''
    return resp.text_annotations[0].description.strip() if resp.text_annotations else ''

# ─────────────────────────────────────────────────────────────
# SANITISATION
# ─────────────────────────────────────────────────────────────

def sanitise(field, raw):
    """Clean OCR output per field type. Returns a plain string."""
    if not raw:
        return ''
    text = raw.replace('\n', ' ').strip()
    text = re.sub(r'\.+$', '', text).strip()

    if field in ('voltage', 'wattage', 'mica_loading_ohms', 'turns_apart'):
        # Keep only the first number-like token
        token = text.split()[0] if text else ''
        return re.sub(r'[^\d.]', '', token).rstrip('.')

    if field in ('mica_cover_qty', 'mica_core_qty'):
        digits = re.sub(r'\D', '', text)
        return str(min(int(digits[0]), 9)) if digits else ''

    if field.endswith('_dims'):
        text = re.sub(r'\s*[xX×*]\s*', ' x ', text)
        return re.sub(r'[^\dx .]', '', text).strip()

    if field == 'legacy_r_number':
        return _format_r_number(text)

    return ' '.join(text.split())


def _format_r_number(raw):
    """Normalise an R-number to R####[A] format."""
    if not raw:
        return ''
    clean = raw.strip().upper()
    m = re.search(r'R?(\d{3,5})([A-Z]?)', clean)
    if m:
        return f"R{m.group(1).zfill(4)}{m.group(2)}"
    return clean


def split_dims(dim_string):
    """
    Split '800 x 40 x 60' → ('800', '40', '60').
    Returns (h, w, d) as strings.  Any missing component is ''.
    """
    if not dim_string:
        return '', '', ''
    parts = [p.strip() for p in re.split(r'[xX×\s]+', dim_string) if p.strip()]
    h = parts[0] if len(parts) > 0 else ''
    w = parts[1] if len(parts) > 1 else ''
    d = parts[2] if len(parts) > 2 else ''
    return h, w, d

# ─────────────────────────────────────────────────────────────
# CONFIDENCE SCORING
# ─────────────────────────────────────────────────────────────

def confidence_score(result):
    score    = 100
    warnings = []

    r_num = result.get('front', {}).get('legacy_r_number', '')
    if not r_num:
        score -= 40; warnings.append('R-number not detected')
    elif not re.match(r'^R\d{4}', r_num):
        score -= 20; warnings.append(f'R-number format unusual: {r_num}')

    if not result.get('front', {}).get('voltage'):
        score -= 10; warnings.append('Voltage missing')
    if not result.get('front', {}).get('wattage'):
        score -= 10; warnings.append('Wattage missing')

    inliers = result.get('alignment_inliers', 0)
    if inliers < 40:
        score -= 15; warnings.append(f'Low alignment confidence ({inliers} inliers)')

    return max(score, 0), warnings

# ─────────────────────────────────────────────────────────────
# CORE PAIR PROCESSOR
# ─────────────────────────────────────────────────────────────

def process_pair(front_path, rear_path):
    """
    Process one front+rear pair.
    Saves warped images and OCR JSON to local disk.
    Returns the path to the JSON file written.
    """
    pair_id   = datetime.now().strftime('%Y%m%d_%H%M%S_') + front_path.stem
    log.info(f"── Processing: {front_path.name}  /  {rear_path.name if rear_path else 'NO REAR'}")

    result = {
        'pair_id':          pair_id,
        'processed_at':     datetime.now().isoformat(),
        'front_original':   front_path.name,
        'rear_original':    rear_path.name if rear_path else None,
        'template_version': None,
        'alignment_inliers': 0,
        'front':            {},
        'rear':             {},
        'confidence':       0,
        'warnings':         [],
    }

    # ── 1. Align front ──────────────────────────────────────
    warped_front, tmpl_key, inliers = align_image(front_path, prefer_rear=False)

    if warped_front is None:
        log.warning(f"  Front alignment failed ({inliers} inliers). Sending to review.")
        result['warnings'].append(f'Front alignment failed ({inliers} inliers)')
        result['confidence'] = 0
        json_path = REVIEW_DIR / f'{pair_id}.json'
        json_path.write_text(json.dumps(result, indent=2))
        return json_path

    result['template_version']    = tmpl_key
    result['alignment_inliers']   = inliers

    # Save warped front
    warped_front_path = WARPED_DIR / f'{pair_id}_F.jpg'
    warped_front.save(str(warped_front_path), quality=92)
    result['warped_front'] = warped_front_path.name

    # ── 2. Align rear ───────────────────────────────────────
    warped_rear = None
    if rear_path:
        rear_tmpl_key          = tmpl_key.replace('front', 'rear')
        warped_rear, _, r_inliers = align_image(rear_path, prefer_rear=True)

        if warped_rear is None or r_inliers < REAR_MIN_INLIERS:
            # Rear cards have little texture — resize raw image to standard canvas
            warped_rear = Image.open(rear_path).convert('RGB').resize(TARGET_SIZE, Image.LANCZOS)
            log.info(f"  Rear alignment weak ({r_inliers} inliers) — resized raw image instead.")

        warped_rear_path = WARPED_DIR / f'{pair_id}_R.jpg'
        warped_rear.save(str(warped_rear_path), quality=92)
        result['warped_rear']     = warped_rear_path.name
        result['rear_tmpl_key']   = rear_tmpl_key

    # ── 3. OCR front fields ─────────────────────────────────
    field_map  = FIELD_MAPS.get(tmpl_key, {})
    front_data = {}

    for field_name, box in field_map.items():
        if field_name == 'pictorial':
            continue   # image crop only — handled separately below

        raw_text = ocr_region(warped_front, box)

        if field_name.endswith('_dims'):
            clean         = sanitise(field_name, raw_text)
            h, w, d       = split_dims(clean)
            base          = field_name.replace('_dims', '')
            front_data[f'{base}_h'] = h
            front_data[f'{base}_w'] = w
            front_data[f'{base}_d'] = d
        else:
            front_data[field_name] = sanitise(field_name, raw_text)

    result['front'] = front_data
    log.info(f"  R-number detected: {front_data.get('legacy_r_number', '(none)')}")

    # ── 4. OCR rear — full page text ────────────────────────
    if warped_rear is not None:
        rear_fmap = FIELD_MAPS.get(result.get('rear_tmpl_key', 'new_rear'), FIELD_MAPS['new_rear'])
        raw_rear  = ocr_region(warped_rear, rear_fmap['full_page'])

        # Strip printed headings, keep handwritten notes
        for heading in REAR_HEADINGS_TO_REMOVE:
            raw_rear = re.sub(r'\b' + re.escape(heading) + r'\b', '', raw_rear, flags=re.IGNORECASE)

        result['rear']['description_private'] = sanitise('description_private', raw_rear)

        # Save upper rear as a pictorial crop (for reference in the review UI)
        pic_box  = rear_fmap.get('pictorial')
        if pic_box:
            rear_pic      = warped_rear.crop(pic_box)
            rear_pic_path = WARPED_DIR / f'{pair_id}_R_PIC.jpg'
            rear_pic.save(str(rear_pic_path), quality=92)
            result['rear_pictorial_crop'] = rear_pic_path.name

    # ── 5. Save front pictorial crop ────────────────────────
    pic_box = field_map.get('pictorial')
    if pic_box:
        pic_crop = warped_front.crop(pic_box)
        pic_path = WARPED_DIR / f'{pair_id}_PIC.jpg'
        pic_crop.save(str(pic_path), quality=92)
        result['pictorial_crop'] = pic_path.name

    # ── 6. Score and write JSON ─────────────────────────────
    score, warnings        = confidence_score(result)
    result['confidence']   = score
    result['warnings']     = warnings

    out_dir   = OCR_JSON_DIR if score >= 70 else REVIEW_DIR
    json_path = out_dir / f'{pair_id}.json'
    json_path.write_text(json.dumps(result, indent=2))

    log.info(f"  Confidence: {score}/100  →  {'ocr_json' if score >= 70 else 'review'}/")
    return json_path

# ─────────────────────────────────────────────────────────────
# GCS  —  FETCH AND ARCHIVE
# ─────────────────────────────────────────────────────────────

def _gcs_client():
    if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(VISION_KEY)
    return storage.Client()


def fetch_pairs_from_gcs():
    """
    Download all image pairs from GCS raw/ folder.
    Returns list of (front_local_path, rear_local_path, front_blob, rear_blob).
    Images are paired by assuming consecutive sorted filenames are front/rear.
    """
    client  = _gcs_client()
    bucket  = client.bucket(GS_BUCKET_NAME)
    blobs   = sorted(
        [b for b in bucket.list_blobs(prefix=GS_RAW_PREFIX)
         if b.name.lower().endswith(('.jpg', '.jpeg', '.png'))],
        key=lambda b: b.name
    )

    if not blobs:
        return []

    # Pair consecutive blobs: [0,1], [2,3], [4,5] ...
    pairs = []
    for i in range(0, len(blobs) - 1, 2):
        front_blob = blobs[i]
        rear_blob  = blobs[i + 1]

        front_name  = front_blob.name.split('/')[-1]
        rear_name   = rear_blob.name.split('/')[-1]
        front_local = RAW_DIR / front_name
        rear_local  = RAW_DIR / rear_name

        log.info(f"Downloading {front_name} + {rear_name} ...")
        front_blob.download_to_filename(str(front_local))
        rear_blob.download_to_filename(str(rear_local))
        pairs.append((front_local, rear_local, front_blob, rear_blob))

    # Handle odd blob (unpaired front — process without rear)
    if len(blobs) % 2 == 1:
        blob        = blobs[-1]
        name        = blob.name.split('/')[-1]
        local       = RAW_DIR / name
        log.info(f"Downloading unpaired front {name} ...")
        blob.download_to_filename(str(local))
        pairs.append((local, None, blob, None))

    return pairs


def archive_in_gcs(front_blob, rear_blob):
    """Move processed originals from raw/ → done/ in GCS."""
    client = _gcs_client()
    bucket = client.bucket(GS_BUCKET_NAME)
    for blob in [front_blob, rear_blob]:
        if blob is None:
            continue
        new_name = GS_DONE_PREFIX + blob.name.split('/')[-1]
        bucket.rename_blob(blob, new_name)
        log.info(f"  Archived GCS: {blob.name} → {new_name}")

# ─────────────────────────────────────────────────────────────
# BATCH RUNNER
# ─────────────────────────────────────────────────────────────

def run_batch():
    """Download all pending pairs from GCS and process them."""
    pairs = fetch_pairs_from_gcs()

    if not pairs:
        log.info("No files in GCS raw/ — nothing to do.")
        return 0

    processed = errors = 0

    for front_local, rear_local, front_blob, rear_blob in pairs:
        try:
            json_path = process_pair(front_local, rear_local)
            log.info(f"  Written: {json_path.name}")

            # Archive in GCS (raw → done) so they don't re-process next run
            archive_in_gcs(front_blob, rear_blob)

            # Clean up local raw copies — warped images stay for Django to serve
            for f in [front_local, rear_local]:
                if f and f.exists():
                    f.unlink()

            processed += 1

        except Exception as exc:
            log.error(f"Error processing {front_local.name}: {exc}")
            log.error(traceback.format_exc())
            errors += 1

    log.info(f"Batch complete — {processed} processed, {errors} errors.")
    return processed


def run_watch(interval=10):
    """Poll GCS every `interval` seconds for new uploads."""
    log.info(f"Watch mode — polling every {interval}s.  Ctrl+C to stop.")
    while True:
        try:
            run_batch()
        except KeyboardInterrupt:
            log.info("Watch mode stopped.")
            break
        except Exception as exc:
            log.error(f"Unexpected error: {exc}")
        time.sleep(interval)

# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    setup_logging()

    parser = argparse.ArgumentParser(description='Heatrex scan processor')
    parser.add_argument('--watch',    action='store_true', help='Poll GCS continuously')
    parser.add_argument('--interval', type=int, default=10, help='Poll interval in seconds')
    args = parser.parse_args()

    if args.watch:
        run_watch(args.interval)
    else:
        run_batch()