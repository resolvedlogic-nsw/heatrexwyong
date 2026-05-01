"""
views_scan_section.py
=====================
Scan inbox views only.  Import these into your main views.py or
include them directly in urls.py.

These replace the scan-related functions in the old views.py.
Everything else (customer views, product views, auth, jobs, printing)
stays in views.py exactly as-is.

URL routes needed in products/urls.py  (already present):
    path('staff/scan-inbox/',                views.scan_inbox,      name='scan_inbox'),
    path('staff/scan-inbox/done/<str:pair_id>/', views.scan_mark_done, name='scan_mark_done'),
"""

import os
import io
import re
import json
import logging
import shutil

from pathlib import Path
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.views.decorators.http import require_POST

from rapidfuzz import process as fuzz_process
from PIL import Image

from .models import (
    Product, Customer, MicaComponent, CaseComponent, ProductFile
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# PATHS  —  single source of truth, derived from settings.MEDIA_ROOT
# ─────────────────────────────────────────────────────────────

def _inbox():
    return Path(settings.MEDIA_ROOT) / 'scan_inbox'

def _ocr_json_dir():
    return _inbox() / 'ocr_json'

def _review_dir():
    return _inbox() / 'review'

def _warped_dir():
    return _inbox() / 'warped'

def _completed_dir():
    return Path(settings.MEDIA_ROOT) / 'completed_scans'

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _warped_url(filename):
    """Return the /media/ URL for a warped image filename."""
    if not filename:
        return None
    return f"{settings.MEDIA_URL}scan_inbox/warped/{filename}"


def _load_next_json():
    """
    Return (data_dict, json_path) for the oldest JSON in ocr_json/.
    Falls back to review/ if ocr_json/ is empty.
    Returns (None, None) if both are empty.
    """
    for folder in [_ocr_json_dir(), _review_dir()]:
        folder.mkdir(parents=True, exist_ok=True)
        files = sorted(folder.glob('*.json'))
        if files:
            path = files[0]
            try:
                data = json.loads(path.read_text())
                return data, path
            except (json.JSONDecodeError, OSError) as exc:
                log.error(f"Could not read {path}: {exc}")
                continue
    return None, None


def _fuzzy_company(raw_name):
    """Return best-matching Customer company_name or raw_name if no match."""
    if not raw_name:
        return ''
    all_names = list(
        Customer.objects.filter(is_active=True).values_list('company_name', flat=True)
    )
    if not all_names:
        return raw_name
    match = fuzz_process.extractOne(raw_name, all_names)
    if match and match[1] >= 60:
        return match[0]
    return raw_name


def _count_queue():
    """Total JSONs waiting across both folders."""
    return (
        len(list(_ocr_json_dir().glob('*.json'))) +
        len(list(_review_dir().glob('*.json')))
    )

# ─────────────────────────────────────────────────────────────
# STAFF AUTH  (import from your main views.py)
# ─────────────────────────────────────────────────────────────
# These are defined in views.py already — only used here via decorator.
# If you move this file to a separate module, import staff_required from views.
from django.contrib.auth.decorators import user_passes_test

def _is_staff(user):
    return user.is_active and (user.is_staff or user.is_superuser)

staff_required = user_passes_test(_is_staff, login_url='login')

# ─────────────────────────────────────────────────────────────
# MAIN VIEW:  scan_inbox
# ─────────────────────────────────────────────────────────────

@staff_required
def scan_inbox(request):
    """
    GET  — Show next card from the JSON queue with warped images + prefilled form.
    POST — Save confirmed data to database, then clean up files.
    """

    # ── POST: save to database ───────────────────────────────
    if request.method == 'POST':
        return _handle_scan_post(request)

    # ── GET: load next card ──────────────────────────────────
    data, json_path = _load_next_json()

    if data is None:
        return render(request, 'staff/scan_inbox.html', {
            'message':        'Inbox empty — all cards processed!',
            'scans_remaining': 0,
        })

    # Apply fuzzy company name match before sending to template
    front = data.get('front', {})
    front['company_name'] = _fuzzy_company(front.get('company_name', ''))
    data['front'] = front

    context = {
        'prefilled':       data,
        'front_url':       _warped_url(data.get('warped_front')),
        'rear_url':        _warped_url(data.get('warped_rear')),
        'front_filename':  data.get('front_original', ''),
        'rear_filename':   data.get('rear_original', ''),
        'scans_remaining': _count_queue(),
        'customers':       Customer.objects.filter(is_active=True).order_by('company_name'),
    }
    return render(request, 'staff/scan_inbox.html', context)


# ─────────────────────────────────────────────────────────────
# POST HANDLER
# ─────────────────────────────────────────────────────────────

def _handle_scan_post(request):
    """Save form data to DB and attach images to the product."""

    r_num = request.POST.get('r_number', '').strip().upper()
    if not r_num:
        messages.error(request, 'R-Number is required.')
        return redirect('scan_inbox')

    pair_id = request.POST.get('pair_id', '').strip()

    with transaction.atomic():

        # ── Product ─────────────────────────────────────────
        product, created = Product.objects.get_or_create(r_number=r_num)
        action = 'Created' if created else 'Updated'

        # Only overwrite fields that have a value coming in
        def _set(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(product, field, val)

        _set('legacy_r_number',   'r_number')   # same value
        _set('voltage',           'voltage')
        _set('wattage',           'wattage')
        _set('element_type',      'element_type')
        _set('die_shape',         'die_shape')
        _set('turns_apart',       'turns_apart')
        _set('description_private', 'description_private')

        product.save()

        # ── Customer linkage ─────────────────────────────────
        company = request.POST.get('company_name', '').strip()
        if company:
            cust = Customer.objects.filter(company_name__iexact=company).first()
            if cust:
                product.customers.add(cust)

        # ── Mica component ───────────────────────────────────
        mica, _ = MicaComponent.objects.get_or_create(product=product)

        def _set_mica(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(mica, field, val)

        _set_mica('cover_dim_h',   'mica_cover_h')
        _set_mica('cover_dim_w',   'mica_cover_w')
        _set_mica('cover_dim_d',   'mica_cover_d')
        _set_mica('cover_quantity','mica_cover_qty')
        _set_mica('core_dim_h',    'mica_core_h')
        _set_mica('core_dim_w',    'mica_core_w')
        _set_mica('core_dim_d',    'mica_core_d')
        _set_mica('core_quantity', 'mica_core_qty')
        _set_mica('loading_ohms',  'mica_loading_ohms')
        _set_mica('wire_type',     'mica_wire_type')
        mica.save()

        # ── Case component ───────────────────────────────────
        case, _ = CaseComponent.objects.get_or_create(product=product)

        def _set_case(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(case, field, val)

        _set_case('inner_dim_h',  'case_inner_h')
        _set_case('inner_dim_w',  'case_inner_w')
        _set_case('inner_dim_d',  'case_inner_d')
        _set_case('outer_dim_h',  'case_outer_h')
        _set_case('outer_dim_w',  'case_outer_w')
        _set_case('outer_dim_d',  'case_outer_d')
        _set_case('sheath_dim_h', 'case_sheath_h')
        _set_case('sheath_dim_w', 'case_sheath_w')
        _set_case('sheath_dim_d', 'case_sheath_d')
        _set_case('wire_grade',   'case_wire_grade')
        case.save()

        # ── Attach warped images as ProductFiles ─────────────
        warped_dir = _warped_dir()

        def _attach_image(filename, label, file_suffix):
            """Save a warped image as a ProductFile on the product."""
            if not filename:
                return
            src = warped_dir / filename
            if not src.exists():
                log.warning(f"Warped image not found: {src}")
                return
            # Skip if this product already has a file with this label
            if product.files.filter(label=label).exists():
                return
            with open(src, 'rb') as fh:
                pf = ProductFile(product=product, file_type='SCAN', label=label)
                pf.file.save(f'{r_num}{file_suffix}', ContentFile(fh.read()), save=True)
            log.info(f"  Attached {label} → {r_num}{file_suffix}")

        # Front and rear full warped scans
        _attach_image(
            request.POST.get('warped_front', ''),
            'Front Scan',
            '_front.jpg',
        )
        _attach_image(
            request.POST.get('warped_rear', ''),
            'Rear Scan',
            '_rear.jpg',
        )
        # Pictorial drawing crop from front
        _attach_image(
            request.POST.get('pictorial_crop', ''),
            'Pictorial Drawing',
            '_pic.jpg',
        )

    # ── Clean up local warped files + JSON ──────────────────
    _cleanup_pair(pair_id)

    messages.success(request, f'{action} {r_num} — {_count_queue()} cards remaining.')
    return redirect('scan_inbox')


def _cleanup_pair(pair_id):
    """
    Delete the JSON and all warped images for this pair_id.
    Called after Andrew confirms a record.
    """
    if not pair_id:
        return

    # Delete JSON from whichever folder it lives in
    for folder in [_ocr_json_dir(), _review_dir()]:
        json_file = folder / f'{pair_id}.json'
        if json_file.exists():
            json_file.unlink()
            log.info(f"  Deleted JSON: {json_file.name}")

    # Delete all warped files for this pair_id
    warped_dir = _warped_dir()
    for f in warped_dir.glob(f'{pair_id}*.jpg'):
        f.unlink()
        log.info(f"  Deleted warped: {f.name}")


# ─────────────────────────────────────────────────────────────
# MARK DONE  (called by JS fetch before form submit)
# ─────────────────────────────────────────────────────────────

@staff_required
@require_POST
def scan_mark_done(request, pair_id):
    """
    POST /staff/scan-inbox/done/<pair_id>/
    Deletes the JSON and warped images for this pair.
    Called by the JS in scan_inbox.html immediately before form submit.
    """
    _cleanup_pair(pair_id)
    from django.http import JsonResponse
    return JsonResponse({'status': 'ok', 'pair_id': pair_id})