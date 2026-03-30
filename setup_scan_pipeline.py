#!/usr/bin/env python3
"""
setup_scan_pipeline.py
======================
Run this ONCE to set up the folder structure and copy template files.

Usage:
    cd /home/heatrexwyong/heatrex
    python setup_scan_pipeline.py

What it does:
1. Creates all required folders under media/scan_inbox/
2. Copies the blank template JPGs to products/scan_templates/
3. Verifies OpenCV and Pillow can load the templates
4. Prints a checklist of what's ready and what still needs doing
"""

import os
import sys
import shutil
from pathlib import Path

BASE_DIR    = Path('/home/heatrexwyong/heatrex')
MEDIA_ROOT  = BASE_DIR / 'media'

FOLDERS = [
    MEDIA_ROOT / 'scan_inbox' / 'raw',
    MEDIA_ROOT / 'scan_inbox' / 'warped',
    MEDIA_ROOT / 'scan_inbox' / 'ocr_json',
    MEDIA_ROOT / 'scan_inbox' / 'review',
    MEDIA_ROOT / 'scan_inbox' / 'done',
    BASE_DIR   / 'products' / 'scan_templates',
]

TEMPLATE_NAMES = [
    'new_front.jpg',
    'old_front.jpg',
    'new_rear.jpg',
    'old_rear.jpg',
]

def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")

def ok(msg):   print(f"  ✅  {msg}")
def warn(msg): print(f"  ⚠️   {msg}")
def err(msg):  print(f"  ❌  {msg}")

def create_folders():
    section("Creating folder structure")
    for folder in FOLDERS:
        folder.mkdir(parents=True, exist_ok=True)
        ok(str(folder))

def check_templates():
    section("Checking template images")
    tmpl_dir = BASE_DIR / 'products' / 'scan_templates'
    all_ok   = True
    for name in TEMPLATE_NAMES:
        path = tmpl_dir / name
        if path.exists():
            try:
                import cv2
                import numpy as np
                from PIL import Image
                pil = Image.open(path)
                arr = np.array(pil.convert('L'))
                orb = cv2.ORB_create(nfeatures=500)
                kps, _ = orb.detectAndCompute(arr, None)
                ok(f"{name}  ({pil.size[0]}x{pil.size[1]}  {len(kps)} ORB keypoints)")
            except Exception as e:
                err(f"{name}  — could not verify: {e}")
                all_ok = False
        else:
            err(f"{name}  — NOT FOUND at {path}")
            all_ok = False
            print(f"       ↳ Upload the blank template image and rename it to: {name}")
    return all_ok

def check_dependencies():
    section("Checking Python dependencies")
    deps = {
        'cv2':                 'opencv-python-headless',
        'numpy':               'numpy',
        'PIL':                 'Pillow',
        'google.cloud.vision': 'google-cloud-vision',
        'rapidfuzz':           'rapidfuzz',
    }
    all_ok = True
    for mod, pkg in deps.items():
        try:
            __import__(mod)
            ok(mod)
        except ImportError:
            # Try a subprocess check with the same interpreter — catches
            # virtualenv vs system Python mismatches on PythonAnywhere
            import subprocess
            result = subprocess.run(
                [sys.executable, '-c', f'import {mod}'],
                capture_output=True
            )
            if result.returncode == 0:
                ok(f"{mod}  (found via {sys.executable})")
            else:
                err(f"{mod}  — install with:  pip3 install {pkg}")
                all_ok = False
    return all_ok

def check_vision_key():
    section("Checking Google Vision credentials")
    key_path = BASE_DIR / 'vision_key.json'
    if key_path.exists():
        ok(f"vision_key.json found at {key_path}")
        return True
    else:
        err(f"vision_key.json NOT FOUND at {key_path}")
        print("       ↳ Download your Google Cloud service account key")
        print("         and place it at that path.")
        return False

def check_processor_script():
    section("Checking processor script")
    proc = BASE_DIR / 'scan_processor.py'
    if proc.exists():
        ok(f"scan_processor.py found")
    else:
        err(f"scan_processor.py NOT FOUND — copy it to {BASE_DIR}")

def print_instructions():
    section("Next steps")
    print("""
  1. UPLOAD TEMPLATE IMAGES
     Put the four blank card scans in:
       /home/heatrexwyong/heatrex/products/scan_templates/
     Named exactly:
       new_front.jpg   new_rear.jpg
       old_front.jpg   old_rear.jpg

  2. REPLACE slicer_test.py
     Copy the new slicer_test.py to:
       /home/heatrexwyong/heatrex/products/slicer_test.py

  3. UPDATE views.py
     Replace the ocr_api_trigger and scan_inbox functions in views.py
     with the versions from views_scan_section.py
     Add these imports to the top of views.py:
       from .slicer_test import align_image_path, ocr_field, sanitize_text, split_dims, FIELD_MAPS

  4. ADD URL for scan_mark_done  (in urls.py):
       path('staff/scan-inbox/done/<str:pair_id>/', views.scan_mark_done, name='scan_mark_done'),

  5. INSTALL MISSING PACKAGES (if any flagged above):
       pip install opencv-python-headless numpy Pillow google-cloud-vision rapidfuzz

  6. TEST ALIGNMENT (no Vision API needed):
       cd /home/heatrexwyong/heatrex
       python scan_processor.py --dry-run

  7. RUN BATCH (with OCR):
       python scan_processor.py

  8. RUN CONTINUOUSLY (watch mode):
       python scan_processor.py --watch --interval 30

  FOLDER STRUCTURE:
    media/scan_inbox/
      raw/        ← drop scanned JPGs here (front+rear pairs in order)
      warped/     ← aligned 1600x1009 images (auto-generated)
      ocr_json/   ← pre-processed OCR results waiting for staff review
      review/     ← low-confidence cards needing manual attention
      done/       ← completed records (auto-archived)
    """)

if __name__ == '__main__':
    print("\n" + "═"*50)
    print("  Heatrex Scan Pipeline Setup")
    print("═"*50)

    create_folders()
    deps_ok   = check_dependencies()
    tmpls_ok  = check_templates()
    vision_ok = check_vision_key()
    check_processor_script()
    print_instructions()

    print("\n" + "═"*50)
    if deps_ok and tmpls_ok and vision_ok:
        print("  ✅  Everything looks ready. Run: python scan_processor.py --dry-run")
    else:
        print("  ⚠️   Some items need attention — see above.")
    print("═"*50 + "\n")
