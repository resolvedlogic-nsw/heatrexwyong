from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Max, Q
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.core.files import File
from django.core.files.base import ContentFile
from django.conf import settings
from django.urls import reverse
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .slicer_test import align_image_path, ocr_field, sanitize_text, split_dims, FIELD_MAPS
import os, io, re
from thefuzz import process
from PIL import Image
from .forms import CustomerForm, ProductForm, MicaComponentForm, CaseComponentForm, GenerateAccountForm
from .models import (Customer, Product, Job, ProductFile, MicaComponent, CaseComponent, PortalProfile, ProductCategory)
from .slicer_test import (map_layout_to_pixels, detect_text_from_bytes, sanitize_text, find_anchor_coordinates, warp_and_crop_image, TEMPLATE_ANCHORS)

# ---------------------------------------------------------
# GOOGLE CLOUD STORAGE (Media Files Vault)
# ---------------------------------------------------------
import os

# Tell Django to use Google Cloud for uploaded/scanned media
DEFAULT_FILE_STORAGE = 'storages.backends.gcloud.GoogleCloudStorage'

# Put the bucket name you created in Step 1 here:
GS_BUCKET_NAME = 'heatrex-card-archives'

# Point it to your existing Vision API Key
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.join(settings.BASE_DIR, 'vision_key.json')

# Never overwrite an old scan if two files happen to have the exact same name
GS_FILE_OVERWRITE = False

# Tell Google to generate temporary secure links that expire in 10 minutes
# (Keeps your customer data highly secure)
GS_DEFAULT_ACL = None

# ─────────────────────────────────────────
# 1. HELPERS & AUTH DECORATORS
# ─────────────────────────────────────────

def is_staff_user(user):
    return user.is_active and (user.is_staff or user.is_superuser)

staff_required = user_passes_test(is_staff_user, login_url='login')


def get_customer_or_none(user):
    if hasattr(user, 'portalprofile'):
        return user.portalprofile.customer
    return None

def _post_login_redirect(user):
    if is_staff_user(user):
        return 'staff_dashboard'
    return 'portal_dashboard'

def get_sorted_files(product):
    """Sorts files: Scan (1), Case (2), Mica (3), Photo (4), Other (5)."""
    order = {'SCAN': 1, 'CASE_DRAWING': 2, 'MICA_DRAWING': 3, 'PHOTO': 4}
    f = list(product.files.all())
    f.sort(key=lambda x: order.get(x.file_type, 5))
    return f

# ─────────────────────────────────────────
# 2. PUBLIC & AUTH VIEWS
# ─────────────────────────────────────────

def home(request):
    return render(request, 'home.html')

def login_view(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request.user))

    if request.method == 'POST':
        u = request.POST.get('username', '').strip()
        p = request.POST.get('password', '')
        user = authenticate(request, username=u, password=p)

        if user is not None:
            login(request, user)
            next_url = request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect(_post_login_redirect(user))
        else:
            messages.error(request, 'Incorrect username or password.')

    return render(request, 'auth/login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

# ─────────────────────────────────────────
# 3. CLIENT PORTAL (CUSTOMER VIEWS)
# ─────────────────────────────────────────

@login_required(login_url='login')
def portal_dashboard(request):
    if is_staff_user(request.user):
        return redirect('staff_dashboard')

    customer = get_customer_or_none(request.user)
    if not customer:
        return render(request, 'portal/no_customer.html')

    products = (
        Product.objects.filter(customers=customer, is_active=True)
        .annotate(job_count=Count('jobs'), last_ordered=Max('jobs__requested_date'))
        .order_by('r_number')
    )

    q = request.GET.get('q', '').strip()
    if q:
        products = products.filter(
            Q(r_number__icontains=q) |
            Q(description_invoice__icontains=q) |
            Q(element_type__icontains=q)
        )

    return render(request, 'portal/dashboard.html', {
        'customer': customer, 'products': products, 'search_query': q, 'product_count': products.count()
    })

@login_required(login_url='login')
def part_detail(request, r_number):
    customer = get_customer_or_none(request.user)
    product = get_object_or_404(Product, r_number=r_number, customers=customer, is_active=True)
    return render(request, 'portal/part_detail.html', {
        'product': product, 'jobs': product.jobs.order_by('-requested_date')[:10], 'files': product.files.all(), 'customer': customer
    })

@login_required(login_url='login')
def reorder_request(request, r_number):
    customer = get_customer_or_none(request.user)
    product = get_object_or_404(Product, r_number=r_number, customers=customer, is_active=True)

    if request.method == 'POST':
        qty = int(request.POST.get('quantity', 1))
        po = request.POST.get('po_number', '').strip()
        notes = request.POST.get('notes', '').strip()
        req_date = request.POST.get('required_date') or None

        last_job = Job.objects.order_by('-id').first()
        next_id = (last_job.id + 1) if last_job else 1
        job_num = f"{timezone.now().year}{next_id:06d}"

        Job.objects.create(
            job_number=job_num, product=product, order_quantity=qty,
            requested_by=request.user, purchase_order=po or None,
            notes=notes or None, required_date=req_date, status='PENDING'
        )

        # Email notification logic here...
        messages.success(request, f'Reorder submitted for {r_number}.')
        return redirect('part_detail', r_number=r_number)

    return render(request, 'portal/reorder.html', {'product': product, 'customer': customer})

@login_required(login_url='login')
def portal_change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Your password was successfully updated!')
        return redirect('portal_dashboard')
    return render(request, 'portal/change_password.html', {'form': form})

# ─────────────────────────────────────────
# 4. STAFF DASHBOARD & CUSTOMER ADMIN
# ─────────────────────────────────────────

@staff_required
def staff_dashboard(request):
    return render(request, 'staff/dashboard.html', {
        'recent_jobs': Job.objects.select_related('product__category').prefetch_related('product__customers').order_by('-requested_date')[:20],
        'pending_jobs': Job.objects.filter(status='PENDING').count(),
        'in_prod_jobs': Job.objects.filter(status='IN_PROD').count(),
        'customer_count': Customer.objects.filter(is_active=True).count(),
        'product_count': Product.objects.filter(is_active=True).count(),
    })

@staff_required
def staff_customer_list(request):
    q = request.GET.get('q', '').strip()
    customers = Customer.objects.filter(is_active=True).annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    )
    if q:
        customers = customers.filter(Q(customer_number__icontains=q) | Q(company_name__icontains=q))
    return render(request, 'staff/customer_list.html', {'customers': customers, 'search_query': q})

@staff_required
def staff_customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    products = Product.objects.filter(customers=customer).annotate(
        job_count=Count('jobs'), last_ordered=Max('jobs__requested_date')
    )
    return render(request, 'staff/customer_detail.html', {'customer': customer, 'products': products})

@staff_required
def staff_customer_create(request):
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, f'Customer {customer.company_name} created.')
            return redirect('staff_customer_detail', pk=customer.pk)
    else:
        existing = Customer.objects.filter(customer_number__istartswith='C').values_list('customer_number', flat=True)
        used_ints = {int(n[1:]) for n in existing if n[1:].isdigit()}
        next_i = 1
        while next_i in used_ints: next_i += 1
        form = CustomerForm(initial={'customer_number': f"C{next_i:06d}"})
    return render(request, 'staff/customer_form.html', {'form': form, 'action': 'Create'})

@staff_required
def staff_customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Customer {customer.company_name} updated.')
        return redirect('staff_customer_detail', pk=customer.pk)
    return render(request, 'staff/customer_form.html', {'form': form, 'customer': customer, 'action': 'Edit'})

# ─────────────────────────────────────────
# 5. SCANNING & OCR ENGINE (THE RECOVERY VERSION)
# ─────────────────────────────────────────

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


def process_product_crops(product, version):
    f_file = product.files.filter(label__icontains="Front").first()
    r_file = product.files.filter(label__icontains="Rear").first()
    f_map = map_layout_to_pixels(f'/home/heatrexwyong/heatrex/front_layout_{version}.csv')
    r_map = map_layout_to_pixels(f'/home/heatrexwyong/heatrex/rear_layout_{version}.csv')

    if f_file and 'pictoral_diagram' in f_map:
        img = warp_and_crop_image(f_file.file.path, f'front_{version}', f_map['pictoral_diagram'])
        if img:
            buf = io.BytesIO(); img.save(buf, format='PNG')
            ProductFile.objects.create(product=product, label="Front Drawing", file_type="SCAN", file=ContentFile(buf.getvalue(), name=f"{product.r_number}_f.png"))

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
    import json
    from thefuzz import process

    ocr_json_dir = os.path.join(settings.MEDIA_ROOT, 'scan_inbox', 'ocr_json')
    json_files = []
    if os.path.isdir(ocr_json_dir):
        json_files = sorted([f for f in os.listdir(ocr_json_dir) if f.endswith('.json')])

    prefilled_data = {}
    if json_files:
        # 1. A processed JSON is ready for staff review!
        json_path = os.path.join(ocr_json_dir, json_files[0])
        with open(json_path) as jf:
            prefilled_data = json.load(jf)

        # --- THE SMART LOOKUPS ---
        front = prefilled_data.get('front', {})

        # A. Company Name Lookup
        comp = front.get('company_name', '').strip()
        if comp:
            all_names = list(Customer.objects.filter(is_active=True).values_list('company_name', flat=True))
            if all_names:
                match = process.extractOne(comp, all_names)
                if match and match[1] >= 60:
                    front['company_name'] = match[0]

        # B. Element Type Lookup (Checks against previously saved products)
        el_type = front.get('element_type', '').strip()
        if el_type:
            # Grabs a unique list of all element types currently in your database
            existing_elements = list(Product.objects.exclude(element_type='').values_list('element_type', flat=True).distinct())
            # Fallback presets just in case the DB is empty
            if not existing_elements:
                existing_elements = ['Mica Band', 'Ceramic Band', 'Cartridge', 'Strip Heater', 'Tubular']

            match = process.extractOne(el_type, existing_elements)
            if match and match[1] >= 60:
                front['element_type'] = match[0]

        prefilled_data['front'] = front
        # ---------------------------

        front_fn = prefilled_data.get('front_original', '')
        rear_fn = prefilled_data.get('rear_original')
        scans_remaining = len(json_files)
    else:
        # 2. No JSONs ready. Are there raw files waiting to be processed?
        scans = sorted([
            f for f in os.listdir(inbox)
            if os.path.splitext(f)[1].lower() in SUPPORTED
            and not f.startswith('PROC_')
        ])

        if not scans:
            return render(request, 'staff/scan_inbox.html', {'message': 'Inbox empty — nothing to process!'})

        front_fn = scans[0]
        rear_fn  = scans[1] if len(scans) > 1 else None
        scans_remaining = max(1, len(scans) // 2)

    context = {
        'front_url':      f"{settings.MEDIA_URL}scan_inbox/raw/{front_fn}",
        'rear_url':       f"{settings.MEDIA_URL}scan_inbox/raw/{rear_fn}" if rear_fn else None,
        'front_filename': front_fn,
        'rear_filename':  rear_fn,
        'scans_remaining': scans_remaining,
        'customers':      Customer.objects.filter(is_active=True).order_by('company_name'),
        'prefilled':      prefilled_data,
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
    Called after staff confirms a record — Deletes the JSON, the RAW scans,
    and the WARPED scans from PythonAnywhere to save space.
    """
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])

    base = os.path.join(settings.MEDIA_ROOT, 'scan_inbox')

    # 1. Delete the JSON file
    json_path = os.path.join(base, 'ocr_json', f'{pair_id}.json')
    if os.path.exists(json_path):
        os.remove(json_path)

    # 2. Delete the Raw files (PROC_002F.jpg, etc)
    raw_dir = os.path.join(base, 'raw')
    if os.path.isdir(raw_dir):
        for fn in os.listdir(raw_dir):
            if pair_id in fn and fn.startswith('PROC_'):
                os.remove(os.path.join(raw_dir, fn))

    # 3. Delete the Warped files
    warped_dir = os.path.join(base, 'warped')
    if os.path.isdir(warped_dir):
        for fn in os.listdir(warped_dir):
            if pair_id in fn:
                os.remove(os.path.join(warped_dir, fn))

    return redirect('scan_inbox')


# ─────────────────────────────────────────
# 6. PRODUCT & JOB MANAGEMENT
# ─────────────────────────────────────────

@staff_required
def staff_product_list(request):
    products = Product.objects.select_related('category').prefetch_related('customers').filter(is_active=True)
    q = request.GET.get('q'); cat = request.GET.get('type')
    if q: products = products.filter(Q(r_number__icontains=q) | Q(customers__company_name__icontains=q)).distinct()
    if cat: products = products.filter(category__code=cat)
    return render(request, 'staff/product_list.html', {
        'products': products.annotate(job_count=Count('jobs'), last_ordered=Max('jobs__requested_date')),
        'categories': ProductCategory.objects.filter(is_active=True), 'search_query': q
    })

@staff_required
def staff_product_create(request):
    p_form = ProductForm(request.POST or None)
    m_form = MicaComponentForm(request.POST or None, prefix='mica')
    c_form = CaseComponentForm(request.POST or None, prefix='case')
    if request.method == 'POST' and p_form.is_valid() and m_form.is_valid() and c_form.is_valid():
        p = p_form.save()
        if m_form.has_changed(): obj = m_form.save(commit=False); obj.product = p; obj.save()
        if c_form.has_changed(): obj = c_form.save(commit=False); obj.product = p; obj.save()
        return redirect('staff_product_detail', r_number=p.r_number)
    else:
        from django.db.models.functions import Cast, Substr
        from django.db.models import IntegerField
        mv = Product.objects.annotate(n=Cast(Substr('r_number', 2), IntegerField())).aggregate(Max('n'))['n__max']
        p_form = ProductForm(initial={'r_number': f"R{mv+1:04d}" if mv else "R0001"})
    return render(request, 'staff/product_form.html', {'product_form': p_form, 'mica_form': m_form, 'case_form': c_form, 'action': 'Create'})

@staff_required
def staff_product_detail(request, r_number):
    product = get_object_or_404(Product, r_number=r_number)
    return render(request, 'staff/product_detail.html', {'product': product, 'jobs': product.jobs.all(), 'files': product.files.all()})

@staff_required
def staff_product_edit(request, r_number):
    p = get_object_or_404(Product, r_number=r_number)
    pf = ProductForm(request.POST or None, instance=p)
    mf = MicaComponentForm(request.POST or None, instance=getattr(p, 'mica_details', None), prefix='mica')
    cf = CaseComponentForm(request.POST or None, instance=getattr(p, 'case_details', None), prefix='case')
    if request.method == 'POST' and pf.is_valid() and mf.is_valid() and cf.is_valid():
        pf.save(); mf.save(); cf.save(); return redirect('staff_product_detail', r_number=r_number)
    return render(request, 'staff/product_form.html', {'product_form': pf, 'mica_form': mf, 'case_form': cf, 'product': p, 'action': 'Edit'})

@staff_required
def staff_job_list(request):
    status_filter = request.GET.get('status', '').strip()
    q = request.GET.get('q', '').strip()
    jobs = Job.objects.select_related('product__category', 'requested_by').prefetch_related('product__customers').order_by('-requested_date')
    if status_filter: jobs = jobs.filter(status=status_filter)
    if q: jobs = jobs.filter(Q(job_number__icontains=q) | Q(product__r_number__icontains=q)).distinct()
    return render(request, 'staff/job_list.html', {'jobs': jobs, 'status_choices': Job.STATUS_CHOICES, 'search_query': q})

@staff_required
def staff_job_create(request, r_number):
    product = get_object_or_404(Product, r_number=r_number)
    if request.method == 'POST':
        qty = int(request.POST.get('quantity', 1))
        last_job = Job.objects.order_by('-id').first()
        job_num = f"{timezone.now().year}{(last_job.id + 1 if last_job else 1):06d}"
        Job.objects.create(job_number=job_num, product=product, order_quantity=qty, requested_by=request.user, status='PENDING')
        return redirect('staff_product_detail', r_number=r_number)
    return render(request, 'staff/job_create.html', {'product': product})

@staff_required
def staff_job_update_status(request, pk):
    j = get_object_or_404(Job, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        j.status = new_status
        if new_status == 'DISPATCHED': j.dispatched_date = timezone.now().date()
        j.save()
    return redirect(request.META.get('HTTP_REFERER', 'staff_job_list'))

# ─────────────────────────────────────────
# 7. PRINTING & ACCOUNT MGMT
# ─────────────────────────────────────────

@staff_required
def staff_product_print_setup(request, r_number):
    p = get_object_or_404(Product, r_number=r_number)
    return render(request, 'staff/print_setup.html', {
        'product': p, 'files': get_sorted_files(p),
        'print_target_url': reverse('staff_product_print', args=[r_number])
    })

@staff_required
def staff_product_print(request, r_number):
    p = get_object_or_404(Product, r_number=r_number)
    ids = request.GET.getlist('file_ids')
    f = [x for x in get_sorted_files(p) if str(x.id) in ids]
    return render(request, 'staff/print_card.html', {
        'product': p, 'files': f, 'orientation': request.GET.get('orientation', 'portrait')
    })

@staff_required
def staff_job_print_setup(request, pk):
    j = get_object_or_404(Job, pk=pk)
    return render(request, 'staff/print_setup.html', {
        'product': j.product, 'job': j, 'files': get_sorted_files(j.product),
        'print_target_url': reverse('staff_job_print', args=[pk])
    })

@staff_required
def staff_job_print(request, pk):
    j = get_object_or_404(Job, pk=pk)
    ids = request.GET.getlist('file_ids')
    f = [x for x in get_sorted_files(j.product) if str(x.id) in ids]
    return render(request, 'staff/print_card.html', {
        'product': j.product, 'job': j, 'files': f, 'orientation': request.GET.get('orientation', 'portrait')
    })

@staff_required
def staff_generate_account(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = GenerateAccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        if User.objects.filter(username=email).exists():
            messages.error(request, 'User with this email already exists.')
        else:
            p = get_random_string(12)
            user = User.objects.create_user(
                username=email, email=email, password=p,
                first_name=form.cleaned_data['first_name'], last_name=form.cleaned_data['last_name']
            )
            PortalProfile.objects.create(user=user, customer=customer, is_principal=True)
            send_mail("Heatrex Portal Access", f"User: {email}\nPass: {p}", 'heatrexwyong@gmail.com', [email])
            messages.success(request, f'Account generated for {email}. Password emailed.')
            return redirect('staff_customer_detail', pk=customer.pk)
    return render(request, 'staff/generate_account.html', {'form': form, 'customer': customer})

@staff_required
def staff_manage_accounts(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        u = get_object_or_404(User, id=request.POST.get('user_id'))
        if action == 'revoke_access': u.delete()
        elif action == 'reset_password':
            p = get_random_string(12); u.set_password(p); u.save()
            send_mail("Password Reset", f"New Pass: {p}", 'heatrexwyong@gmail.com', [u.email])
        return redirect('staff_manage_accounts', pk=customer.pk)
    return render(request, 'staff/manage_accounts.html', {'customer': customer, 'profiles': customer.portal_users.all()})

@staff_required
def staff_change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Password changed.')
        return redirect('staff_dashboard')
    return render(request, 'staff/change_password.html', {'form': form})

@staff_required
@require_POST
def staff_product_delete(request, r_number):
    get_object_or_404(Product, r_number=r_number).delete()
    messages.success(request, f"Product {r_number} deleted.")
    return redirect('staff_product_list')