"""
slicer_test.py
==============
Alignment and OCR helpers used by Django views (scan_inbox, ocr_api_trigger).
This replaces the previous version that used OCR-anchor-based warping.

Place at:  /home/heatrexwyong/heatrex/products/slicer_test.py
"""

import os
import io
import re
import logging

import cv2
import numpy as np
from PIL import Image
from google.cloud import vision as gv
from django.conf import settings

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

TARGET_SIZE   = (1600, 1009)
MIN_INLIERS   = 25
TEMPLATES_DIR = os.path.join(settings.BASE_DIR, 'products', 'scan_templates')

# ─────────────────────────────────────────────────────────────
# FIELD MAPS  (kept here so Django views can import them)
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
        'description_private': (3, 903, 1595, 1004),
    },
    'old_rear': {
        'description_private': (3, 903, 1595, 1004),
    },
}

# Legacy alias so existing views that import TEMPLATE_ANCHORS don't break
TEMPLATE_ANCHORS = {}

# ─────────────────────────────────────────────────────────────
# TEMPLATE CACHE
# ─────────────────────────────────────────────────────────────

_template_cache = {}

def _load_template(name):
    if name not in _template_cache:
        path = os.path.join(TEMPLATES_DIR, f'{name}.jpg')
        if not os.path.exists(path):
            raise FileNotFoundError(f"Scan template missing: {path}")
        pil = Image.open(path).convert('L')
        _template_cache[name] = np.array(pil)
    return _template_cache[name]

# ─────────────────────────────────────────────────────────────
# ALIGNMENT
# ─────────────────────────────────────────────────────────────

def align_image_bytes(img_bytes, prefer_rear=False):
    """
    Align raw image bytes to the canonical canvas.

    Args:
        img_bytes:    raw bytes of the scan image
        prefer_rear:  if True, try rear templates first

    Returns:
        (warped_pil, template_key, inlier_count)
    """
    nparr     = np.frombuffer(img_bytes, np.uint8)
    scan_bgr  = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    scan_gray = cv2.cvtColor(scan_bgr, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(nfeatures=4000)
    bf  = cv2.BFMatcher(cv2.NORM_HAMMING)

    kp_scan, desc_scan = orb.detectAndCompute(scan_gray, None)
    if desc_scan is None:
        return None, None, 0

    order = (['new_rear', 'old_rear', 'new_front', 'old_front']
             if prefer_rear else
             ['new_front', 'old_front', 'new_rear', 'old_rear'])

    best_inliers = 0
    best_H       = None
    best_key     = None

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
            best_inliers = inliers
            best_H       = H
            best_key     = tmpl_key

    if best_H is None or best_inliers < MIN_INLIERS:
        return None, best_key, best_inliers

    warped_bgr = cv2.warpPerspective(scan_bgr, best_H, TARGET_SIZE)
    warped_pil = Image.fromarray(cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2RGB))
    return warped_pil, best_key, best_inliers


def align_image_path(file_path, prefer_rear=False):
    """Convenience wrapper that reads from a filesystem path."""
    with open(file_path, 'rb') as f:
        img_bytes = f.read()
    return align_image_bytes(img_bytes, prefer_rear=prefer_rear)


# Keep old function name so any remaining imports don't break
def warp_and_crop_image(input_path, template_key, crop_box):
    """Legacy shim — aligns image and returns a cropped PIL image."""
    warped, _, inliers = align_image_path(input_path)
    if warped is None:
        return None
    return warped.crop(crop_box)


# ─────────────────────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────────────────────

_vision_client = None

def _get_client():
    global _vision_client
    if _vision_client is None:
        key_path = os.path.join(settings.BASE_DIR, 'vision_key.json')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = key_path
        _vision_client = gv.ImageAnnotatorClient()
    return _vision_client


def detect_text_from_bytes(content):
    """Run Vision API document_text_detection on raw bytes. Returns string."""
    client = _get_client()
    image  = gv.Image(content=content)
    resp   = client.document_text_detection(image=image)
    if resp.error.message:
        log.error(f"Vision API: {resp.error.message}")
        return ''
    return resp.text_annotations[0].description.strip() if resp.text_annotations else ''


def ocr_field(warped_pil, box):
    """Crop warped_pil to box and OCR it. Returns raw string."""
    crop = warped_pil.crop(box)
    w, h = crop.size
    # Upscale tiny crops for better accuracy
    if w < 200 or h < 50:
        scale = max(200 / w, 50 / h, 1)
        crop  = crop.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format='PNG')
    return detect_text_from_bytes(buf.getvalue())


# ─────────────────────────────────────────────────────────────
# SANITISATION
# ─────────────────────────────────────────────────────────────

def sanitize_text(field_name, raw_text):
    """
    Clean OCR output per field rules.
    Kept as sanitize_text (US spelling) for backwards compatibility.
    """
    if not raw_text:
        return ''
    text = raw_text.replace('\n', ' ').strip()

    if field_name in ('voltage', 'wattage', 'mica_loading_ohms', 'turns_apart'):
        return re.sub(r'[^\d.]', '', text.split()[0] if text else '')

    if field_name in ('mica_cover_qty', 'mica_core_qty'):
        digits = re.sub(r'\D', '', text)
        return str(int(digits[0])) if digits else ''

    if field_name.endswith('_dims'):
        text = re.sub(r'\s*[xX×\*]\s*', ' x ', text)
        text = re.sub(r'[^\dx .]', '', text).strip()
        return text

    if field_name == 'legacy_r_number':
        return _format_r_number(text)

    return ' '.join(text.split())


def _format_r_number(raw):
    if not raw:
        return ''
    clean = raw.strip().upper()
    m = re.search(r'R?(\d{3,5})([A-Z]?)', clean)
    if m:
        return f"R{m.group(1).zfill(4)}{m.group(2)}"
    return clean


def split_dims(dim_string):
    """Split '800 x 40 x 60' → ('800', '40', '60'). Returns (h, w, d)."""
    if not dim_string:
        return '', '', ''
    parts = [p.strip() for p in re.split(r'[xX×\s]+', dim_string) if p.strip()]
    return (
        parts[0] if len(parts) > 0 else '',
        parts[1] if len(parts) > 1 else '',
        parts[2] if len(parts) > 2 else '',
    )


