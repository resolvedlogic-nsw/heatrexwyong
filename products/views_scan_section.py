import json
import logging
import re

from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from .models import Customer, MicaComponent, CaseComponent, Product, ProductFile

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

def _is_staff(user):
    return user.is_active and (user.is_staff or user.is_superuser)

staff_required = user_passes_test(_is_staff, login_url='login')


# ─────────────────────────────────────────────────────────────
# PATH HELPERS
# ─────────────────────────────────────────────────────────────

def _inbox():
    return Path(settings.MEDIA_ROOT) / 'scan_inbox'

def _ocr_json_dir():
    return _inbox() / 'ocr_json'

def _review_dir():
    return _inbox() / 'review'

def _warped_dir():
    return Path(settings.MEDIA_ROOT) / 'scan_inbox' / 'warped'


def _warped_url(filename):
    if not filename:
        return None
    return f"{settings.MEDIA_URL}scan_inbox/warped/{filename}"


def _count_queue():
    return (
        len(list(_ocr_json_dir().glob('*.json'))) +
        len(list(_review_dir().glob('*.json')))
    )


def _load_next_json():
    """
    Return (data_dict, json_path) for the oldest JSON in ocr_json/.
    Falls back to review/ if ocr_json/ is empty.
    Returns (None, None) if both empty.
    """
    for folder in [_ocr_json_dir(), _review_dir()]:
        folder.mkdir(parents=True, exist_ok=True)
        files = sorted(folder.glob('*.json'))
        if files:
            path = files[0]
            try:
                return json.loads(path.read_text()), path
            except (json.JSONDecodeError, OSError) as exc:
                log.error(f"Could not read {path}: {exc}")
    return None, None


# ─────────────────────────────────────────────────────────────
# COMPANY LOOKUP  —  by R-number, NOT fuzzy name match
# ─────────────────────────────────────────────────────────────

def _lookup_company_by_r_number(r_number):
    """
    Return the confirmed company name for an R-number already in the DB.
    Returns '' if not found — the OCR name stays as-is in that case.
    This is intentional: we never overwrite OCR with a fuzzy guess.
    """
    if not r_number:
        return ''
    product = Product.objects.filter(r_number=r_number).first()
    if not product:
        return ''
    customer = product.customers.first()
    return customer.company_name if customer else ''


# ─────────────────────────────────────────────────────────────
# SCAN INBOX  —  GET
# ─────────────────────────────────────────────────────────────

@staff_required
def scan_inbox(request):
    if request.method == 'POST':
        return _handle_scan_post(request)

    data, json_path = _load_next_json()

    if data is None:
        return render(request, 'staff/scan_inbox.html', {
            'message':         'Inbox empty — all cards processed! 🎉',
            'scans_remaining': 0,
        })

    front = data.get('front', {})

    # Look up confirmed company name from DB using R-number.
    # Only substitute if the DB has one AND OCR found none.
    r_num          = front.get('legacy_r_number', '')
    db_company     = _lookup_company_by_r_number(r_num)
    ocr_company    = front.get('company_name', '').strip()
    front['company_name'] = ocr_company or db_company

    # Flag whether this is a known DB record so JS can apply correct glow
    data['is_db_match'] = bool(
        r_num and Product.objects.filter(r_number=r_num).exists()
    )

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
# SCAN INBOX  —  POST (save confirmed data)
# ─────────────────────────────────────────────────────────────

def _handle_scan_post(request):
    r_num = request.POST.get('r_number', '').strip().upper()
    if not r_num:
        messages.error(request, 'R-Number is required.')
        return redirect('scan_inbox')

    pair_id = request.POST.get('pair_id', '').strip()

    with transaction.atomic():

        # ── Product ─────────────────────────────────────────
        product, created = Product.objects.get_or_create(r_number=r_num)

        def _set(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(product, field, val)

        _set('legacy_r_number',    'r_number')
        _set('voltage',            'voltage')
        _set('wattage',            'wattage')
        _set('element_type',       'element_type')
        _set('die_shape',          'die_shape')
        _set('turns_apart',        'turns_apart')
        _set('description_private','description_private')
        product.save()

        # ── Customer ─────────────────────────────────────────
        company = request.POST.get('company_name', '').strip()
        if company:
            cust = Customer.objects.filter(company_name__iexact=company).first()
            if cust:
                product.customers.add(cust)

        # ── Mica ─────────────────────────────────────────────
        mica, _ = MicaComponent.objects.get_or_create(product=product)

        def _set_mica(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(mica, field, val)

        _set_mica('cover_dim_h',    'mica_cover_h')
        _set_mica('cover_dim_w',    'mica_cover_w')
        _set_mica('cover_quantity', 'mica_cover_qty')
        _set_mica('core_dim_h',     'mica_core_h')
        _set_mica('core_dim_w',     'mica_core_w')
        _set_mica('core_quantity',  'mica_core_qty')
        _set_mica('loading_ohms',   'mica_loading_ohms')
        _set_mica('wire_type',      'mica_wire_type')   # assembled by JS
        mica.save()

        # ── Case ─────────────────────────────────────────────
        # Inner dims use h1/h2 (bracket) fields; outer and sheath use h1/w1 only
        case, _ = CaseComponent.objects.get_or_create(product=product)

        def _set_case(field, post_key):
            val = request.POST.get(post_key, '').strip()
            if val:
                setattr(case, field, val)

        # Inner — store as "975(11)" if bracket present, else just "975"
        def _assemble_dim(main_key, bracket_key):
            main    = request.POST.get(main_key, '').strip()
            bracket = request.POST.get(bracket_key, '').strip()
            if main and bracket:
                return f'{main}({bracket})'
            return main

        inner_h = _assemble_dim('case_inner_h1', 'case_inner_h2')
        inner_w = _assemble_dim('case_inner_w1', 'case_inner_w2')
        if inner_h: case.inner_dim_h = inner_h
        if inner_w: case.inner_dim_w = inner_w

        outer_h = request.POST.get('case_outer_h1', '').strip()
        outer_w = request.POST.get('case_outer_w1', '').strip()
        if outer_h: case.outer_dim_h = outer_h
        if outer_w: case.outer_dim_w = outer_w

        sheath_h = request.POST.get('case_sheath_h1', '').strip()
        sheath_w = request.POST.get('case_sheath_w1', '').strip()
        if sheath_h: case.sheath_dim_h = sheath_h
        if sheath_w: case.sheath_dim_w = sheath_w

        _set_case('wire_grade', 'case_wire_grade')
        case.save()

        # ── Images → ProductFile → GCS ───────────────────────
        warped_dir = _warped_dir()

        def _attach(filename, label, suffix):
            if not filename:
                return
            
            clean_name = filename.split('/')[-1]
            src = _warped_dir() / clean_name
            
            if not src.exists():
                # Try the parent directory as a fallback
                src = _warped_dir().parent / clean_name
                if not src.exists():
                    log.warning(f"File not found: {src}")
                    return

            if product.files.filter(label=label).exists():
                return

            with open(src, 'rb') as fh:
                # Capture the binary data once
                file_data = fh.read()
                if not file_data:
                    log.warning(f"File is empty: {src}")
                    return
                
                # Wrap it in ContentFile and give it the correct R-number name
                content = ContentFile(file_data)
                
                pf = ProductFile(
                    product=product, 
                    file_type='SCAN', 
                    label=label
                )
                # Save the file to GCS
                pf.file.save(f"{r_num}{suffix}", content, save=True)
                log.info(f"Successfully attached {label} to {r_num}")

        _attach(request.POST.get('warped_front', ''),        'Front Scan',        '_front.jpg')
        _attach(request.POST.get('warped_rear', ''),         'Rear Scan',         '_rear.jpg')
        _attach(request.POST.get('pictorial_crop', ''),      'Pictorial Drawing', '_pic.jpg')
        _attach(request.POST.get('rear_pictorial_crop', ''), 'Rear Diagram',      '_rear_pic.jpg')

    _cleanup_pair(pair_id)
    action = 'Created' if created else 'Updated'
    messages.success(request, f'{action} {r_num} — {_count_queue()} cards remaining.')
    return redirect('scan_inbox')


# ─────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────

def _cleanup_pair(pair_id):
    """Delete JSON and all warped images for this pair_id."""
    if not pair_id:
        return
    for folder in [_ocr_json_dir(), _review_dir()]:
        f = folder / f'{pair_id}.json'
        if f.exists():
            f.unlink()
            log.info(f"  Deleted JSON: {f.name}")
    for f in _warped_dir().glob(f'{pair_id}*.jpg'):
        f.unlink()
        log.info(f"  Deleted warped: {f.name}")


@staff_required
@require_POST
def scan_mark_done(request, pair_id):
    """Called by JS fetch before form submit to clean up files."""
    #_cleanup_pair(pair_id)
    return JsonResponse({'status': 'ok', 'pair_id': pair_id})


# ─────────────────────────────────────────────────────────────
# API  —  Product lookup by R-number (used by Fetch DB button)
# ─────────────────────────────────────────────────────────────

@staff_required
def api_product_lookup(request, r_number):
    """
    GET /api/product/<r_number>/
    Returns confirmed product data from the database.
    Used by the Fetch DB button in scan_inbox to pre-fill known fields.
    """
    r_number = r_number.strip().upper()
    product  = Product.objects.filter(r_number=r_number).first()

    if not product:
        return JsonResponse({'found': False})

    customer = product.customers.first()
    return JsonResponse({
        'found':        True,
        'r_number':     product.r_number,
        'company_name': customer.company_name if customer else '',
        'element_type': product.element_type or '',
        'voltage':      product.voltage or '',
        'wattage':      product.wattage or '',
        'die_shape':    product.die_shape or '',
    })
