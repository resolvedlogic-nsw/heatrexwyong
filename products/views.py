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
import os, io, re
from PIL import Image
from .forms import CustomerForm, ProductForm, MicaComponentForm, CaseComponentForm, GenerateAccountForm
from .models import (Customer, Product, Job, ProductFile, MicaComponent, CaseComponent, PortalProfile, ProductCategory)
from .slicer_test import (align_image_path, ocr_field, sanitize_text, split_dims, FIELD_MAPS, detect_text_from_bytes, map_layout_to_pixels, find_anchor_coordinates, warp_and_crop_image, TEMPLATE_ANCHORS,)
from rapidfuzz import process as fuzz_process 
from .views_scan_section import scan_inbox, scan_mark_done

# ---------------------------------------------------------
# GOOGLE CLOUD STORAGE (Media Files Vault)
# ---------------------------------------------------------
import os

# Point it to your existing Vision API Key
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = os.path.join(settings.BASE_DIR, 'vision_key.json')


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

  
# ─────────────────────────────────────────────────────────────
# SCAN INBOX VIEW  —  loads next pair from queue
# ─────────────────────────────────────────────────────────────



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