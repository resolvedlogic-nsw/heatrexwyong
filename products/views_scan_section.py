"""
views_scan_section.py
=====================
This file contains ONLY the updated scanning-related views.
Copy these functions into your existing views.py, replacing
the old versions of ocr_api_trigger, scan_inbox, and
process_product_crops.

The rest of your views.py (auth, portal, staff CRUD etc.)
remains unchanged.
"""

# ── These imports already exist in your views.py ──
# from django.shortcuts import render, redirect
# from django.contrib.auth.decorators import user_passes_test
# from django.http import JsonResponse
# from django.conf import settings
# from django.db import transaction
# from django.core.files import File
# from django.core.files.base import ContentFile
# import os, io
# from .models import (Product, Customer, MicaComponent, CaseComponent, ProductFile)

# ── New imports needed at the top of views.py ──
# from .slicer_test import (
#     align_image_path, ocr_field, sanitize_text, split_dims,
#     FIELD_MAPS
# )
# from thefuzz import process as fuzz_process   # already in your requirements


import os
import io
import json
from pathlib import Path

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse
from django.conf import settings
from django.db import transaction
from django.core.files import File
from django.core.files.base import ContentFile

from .models import Product, Customer, MicaComponent, CaseComponent, ProductFile
from .slicer_test import (
    align_image_path, ocr_field, sanitize_text, split_dims, FIELD_MAPS
)

try:
    from thefuzz import process as fuzz_process
except ImportError:
    from rapidfuzz import process as fuzz_process


def is_staff_user(user):
    return user.is_active and (user.is_staff or user.is_superuser)

staff_required = user_passes_test(is_staff_user, login_url='login')


# ─────────────────────────────────────────────────────────────
# OCR API  —  called by the SCAN button in scan_inbox.html
# ─────────────────────────────────────────────────────────────

@staff_required
def ocr_api_trigger(request):
    """
    GET  /staff/ocr-api/?front=FILENAME&rear=FILENAME

    1. Loads the files from scan_inbox/raw/ (or scan_inbox/ for backwards compat)
    2. Aligns them using ORB homography
    3. OCRs each field region
    4. Returns JSON that the JavaScript injects into the form
    """
    front_name = request.GET.get('front', '').strip()
    rear_name  = request.GET.get('rear', '').strip()

    # Support both old path (scan_inbox/) and new path (scan_inbox/raw/)
    inbox_dirs = [
        os.path.join(settings.MEDIA_ROOT, 'scan_inbox', 'raw'),
        os.path.join(settings.MEDIA_ROOT, 'scan_inbox'),
    ]

    def find_file(name):
        for d in inbox_dirs:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        return None

    results = {'front': {}, 'rear': {}, 'template_version': None, 'confidence': 0}

    # ── Align & OCR front ────────────────────────────────────
    if front_name:
        front_path = find_file(front_name)
        if not front_path:
            return JsonResponse({'error': f'Front file not found: {front_name}'}, status=404)

        warped, tmpl_key, inliers = align_image_path(front_path, prefer_rear=False)
        results['template_version'] = tmpl_key
        results['alignment_inliers'] = inliers

        if warped is None:
            return JsonResponse({
                'error': f'Alignment failed (only {inliers} inliers). '
                         'Try a cleaner scan or check template files.',
                'front': {}, 'rear': {}
            }, status=200)   # 200 so JS can still show the error message

        field_map = FIELD_MAPS.get(tmpl_key, {})

        for field_name, box in field_map.items():
            if field_name == 'pictorial':
                continue
            raw_text = ocr_field(warped, box)
            clean    = sanitize_text(field_name, raw_text)

            if field_name.endswith('_dims'):
                # Return as comma-separated so JS splitter can distribute to H/W/D fields
                # Format: "mica_cover_w,mica_cover_h,mica_cover_d" → value "800 x 40 x 60"
                base   = field_name.replace('_dims', '')
                w, h, d = split_dims(clean)
                # Use the multi-target key format the existing JS understands
                # e.g. key = "mica_cover_h,mica_cover_w,mica_cover_d"
                combo_key = f'{base}_h,{base}_w,{base}_d'
                results['front'][combo_key] = f'{w} {h} {d}'.strip()
            else:
                results['front'][field_name] = clean

        # Fuzzy match company name
        comp = results['front'].get('company_name', '').strip()
        if comp:
            all_names = list(Customer.objects.filter(is_active=True)
                             .values_list('company_name', flat=True))
            match = fuzz_process.extractOne(comp, all_names)
            if match and match[1] >= 60:
                results['front']['company_name'] = match[0]

        # Confidence hint for the UI
        r_num = results['front'].get('legacy_r_number', '')
        results['confidence'] = inliers
        results['r_number_found'] = bool(r_num)

    # ── Align & OCR rear ─────────────────────────────────────
    if rear_name and rear_name.lower() not in ('none', ''):
        rear_path = find_file(rear_name)
        if rear_path:
            rear_warped, rear_key, _ = align_image_path(rear_path, prefer_rear=True)

            # Determine which rear field map to use
            if rear_key and 'rear' in rear_key:
                rear_fmap = FIELD_MAPS[rear_key]
            else:
                # Fall back: use bottom strip of raw image
                from PIL import Image
                raw_img    = Image.open(rear_path).convert('RGB')
                rw, rh     = raw_img.size
                rear_warped = raw_img
                rear_fmap  = {'description_private': (0, int(rh * 0.88), rw, rh)}

            if rear_warped:
                for field_name, box in rear_fmap.items():
                    raw_text = ocr_field(rear_warped, box)
                    results['rear'][field_name] = sanitize_text(field_name, raw_text)

    return JsonResponse(results)


# ─────────────────────────────────────────────────────────────
# SCAN INBOX VIEW  —  loads next pair from queue
# ─────────────────────────────────────────────────────────────

@staff_required
def scan_inbox(request):
    """
    GET  — show the next unprocessed pair from the queue
    POST — save OCR-confirmed data to database
    """
    # Determine inbox location (prefer new raw/ subfolder)
    inbox_raw  = os.path.join(settings.MEDIA_ROOT, 'scan_inbox', 'raw')
    inbox_old  = os.path.join(settings.MEDIA_ROOT, 'scan_inbox')
    inbox      = inbox_raw if os.path.isdir(inbox_raw) else inbox_old

    SUPPORTED = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

    # ── POST: save to database ───────────────────────────────
    if request.method == 'POST':
        r_num = request.POST.get('r_number', '').strip().upper()
        ver   = request.POST.get('template_version', 'new')

        with transaction.atomic():
            product, _ = Product.objects.get_or_create(r_number=r_num)

            product.voltage             = request.POST.get('voltage') or product.voltage
            product.wattage             = request.POST.get('wattage') or product.wattage
            product.element_type        = request.POST.get('element_type') or product.element_type
            product.die_shape           = request.POST.get('die_shape') or product.die_shape
            product.description_private = request.POST.get('description_private', '')

            # Customer linkage
            company = request.POST.get('company_name', '').strip()
            if company:
                cust = Customer.objects.filter(company_name__iexact=company).first()
                if cust:
                    product.customers.add(cust)

            product.save()

            # ── MICA ──
            mica, _ = MicaComponent.objects.get_or_create(product=product)
            mica.cover_dim_w   = request.POST.get('mica_cover_w') or mica.cover_dim_w
            mica.cover_dim_h   = request.POST.get('mica_cover_h') or mica.cover_dim_h
            mica.cover_dim_d   = request.POST.get('mica_cover_d') or mica.cover_dim_d
            mica.cover_quantity= request.POST.get('mica_cover_qty') or mica.cover_quantity
            mica.core_dim_w    = request.POST.get('mica_core_w') or mica.core_dim_w
            mica.core_dim_h    = request.POST.get('mica_core_h') or mica.core_dim_h
            mica.core_dim_d    = request.POST.get('mica_core_d') or mica.core_dim_d
            mica.core_quantity = request.POST.get('mica_core_qty') or mica.core_quantity
            mica.loading_ohms  = request.POST.get('mica_loading_ohms') or mica.loading_ohms
            mica.wire_type     = request.POST.get('mica_wire_type') or mica.wire_type
            mica.save()

            # ── CASE ──
            case, _ = CaseComponent.objects.get_or_create(product=product)
            case.inner_dim_w  = request.POST.get('case_inner_w') or case.inner_dim_w
            case.inner_dim_h  = request.POST.get('case_inner_h') or case.inner_dim_h
            case.inner_dim_d  = request.POST.get('case_inner_d') or case.inner_dim_d
            case.outer_dim_w  = request.POST.get('case_outer_w') or case.outer_dim_w
            case.outer_dim_h  = request.POST.get('case_outer_h') or case.outer_dim_h
            case.outer_dim_d  = request.POST.get('case_outer_d') or case.outer_dim_d
            case.sheath_dim_w = request.POST.get('case_sheath_w') or case.sheath_dim_w
            case.sheath_dim_h = request.POST.get('case_sheath_h') or case.sheath_dim_h
            case.sheath_dim_d = request.POST.get('case_sheath_d') or case.sheath_dim_d
            case.wire_grade   = request.POST.get('case_wire_grade') or case.wire_grade
            case.save()

            # ── Save master image files ──
            for fn_key, suffix, label in [
                ('front_filename', '_f.jpg', 'Front Scan'),
                ('rear_filename',  '_r.jpg', 'Rear Scan'),
            ]:
                fn = request.POST.get(fn_key, '').strip()
                if fn:
                    fp = os.path.join(inbox, fn)
                    # Also check warped/ folder
                    warped_dir = os.path.join(settings.MEDIA_ROOT, 'scan_inbox', 'warped')
                    warped_fn  = fn.replace('.jpg', '_warped.jpg')
                    warped_fp  = os.path.join(warped_dir, warped_fn)

                    save_path = warped_fp if os.path.exists(warped_fp) else fp
                    if os.path.exists(save_path):
                        with open(save_path, 'rb') as f:
                            pf = ProductFile(
                                product=product,
                                file_type='SCAN',
                                label=label
                            )
                            pf.file.save(f'{r_num}{suffix}', File(f), save=True)
                        # Clean up processed file
                        if os.path.exists(fp):
                            os.remove(fp)

            # ── Save pictorial crop ──
            front_fn = request.POST.get('front_filename', '').strip()
            if front_fn:
                _save_pictorial_crop(product, front_fn, ver, inbox)

        from django.contrib import messages
        messages.success(request, f'Record {r_num} saved successfully.')
        return redirect('scan_inbox')

    # ── GET: load next pair ──────────────────────────────────
    scans = sorted([
        f for f in os.listdir(inbox)
        if os.path.splitext(f)[1].lower() in SUPPORTED
        and not f.startswith('PROC_')
    ])

    if not scans:
        return render(request, 'staff/scan_inbox.html', {'message': 'Inbox empty — nothing to process!'})

    # Check if there's a pre-processed JSON available
    ocr_json_dir  = os.path.join(settings.MEDIA_ROOT, 'scan_inbox', 'ocr_json')
    prefilled_data = {}
    if os.path.isdir(ocr_json_dir):
        json_files = sorted(Path(ocr_json_dir).glob('*.json'))
        if json_files:
            with open(json_files[0]) as jf:
                prefilled_data = json.load(jf)

    front_fn = scans[0]
    rear_fn  = scans[1] if len(scans) > 1 else None

    context = {
        'front_url':      f"{settings.MEDIA_URL}scan_inbox/raw/{front_fn}",
        'rear_url':       f"{settings.MEDIA_URL}scan_inbox/raw/{rear_fn}" if rear_fn else None,
        'front_filename': front_fn,
        'rear_filename':  rear_fn,
        'scans_remaining': len(scans),
        'customers':      Customer.objects.filter(is_active=True).order_by('company_name'),
        'prefilled':      prefilled_data,   # optional — JS can use this
    }
    return render(request, 'staff/scan_inbox.html', context)


# ─────────────────────────────────────────────────────────────
# HELPER: save pictorial crop
# ─────────────────────────────────────────────────────────────

def _save_pictorial_crop(product, front_filename, ver, inbox):
    """Align front image and save the pictorial drawing region as a ProductFile."""
    from PIL import Image as PILImage

    fp = os.path.join(inbox, front_filename)
    if not os.path.exists(fp):
        return

    tmpl_key = f'{ver}_front' if ver in ('new', 'old') else ver
    warped, detected_key, inliers = align_image_path(fp)
    if warped is None:
        return

    use_key   = detected_key or tmpl_key
    field_map = FIELD_MAPS.get(use_key, {})
    pic_box   = field_map.get('pictorial')
    if not pic_box:
        return

    crop = warped.crop(pic_box)
    buf  = io.BytesIO()
    crop.save(buf, format='PNG')
    buf.seek(0)

    # Don't overwrite if one already exists
    if not product.files.filter(file_type='SCAN', label='Pictorial Drawing').exists():
        pf = ProductFile(product=product, file_type='SCAN', label='Pictorial Drawing')
        pf.file.save(f'{product.r_number}_pic.png', ContentFile(buf.getvalue()), save=True)


# ─────────────────────────────────────────────────────────────
# BATCH JSON IMPORT  —  reads pre-processed JSON from processor
# ─────────────────────────────────────────────────────────────

@staff_required
def scan_mark_done(request, pair_id):
    """
    POST /staff/scan-inbox/done/<pair_id>/
    Called after staff confirms a record — moves the JSON to done/
    and marks raw files as done.
    """
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])

    base         = os.path.join(settings.MEDIA_ROOT, 'scan_inbox')
    ocr_json_dir = os.path.join(base, 'ocr_json')
    done_dir     = os.path.join(base, 'done')
    os.makedirs(done_dir, exist_ok=True)

    json_path = os.path.join(ocr_json_dir, f'{pair_id}.json')
    if os.path.exists(json_path):
        import shutil
        shutil.move(json_path, os.path.join(done_dir, f'{pair_id}.json'))

    # Also move PROC_ raw files to done/
    raw_dir = os.path.join(base, 'raw')
    if os.path.isdir(raw_dir):
        for fn in os.listdir(raw_dir):
            if pair_id in fn and fn.startswith('PROC_'):
                import shutil
                shutil.move(
                    os.path.join(raw_dir, fn),
                    os.path.join(done_dir, fn)
                )

    return redirect('scan_inbox')
