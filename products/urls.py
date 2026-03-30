from django.urls import path
from . import views



urlpatterns = [
    # ── Front end ──────────────────────────────────────────
    path('', views.home, name='home'),

    # ── Auth ──────────────────────────────────────────
    path('login/',  views.login_view,  name='login'),
    path('logout/', views.logout_view, name='logout'),

    # ── Client Portal ──────────────────────────────────
    path('portal/',                              views.portal_dashboard, name='portal_dashboard'),
    path('portal/part/<str:r_number>/',          views.part_detail,      name='part_detail'),
    path('portal/part/<str:r_number>/reorder/',  views.reorder_request,  name='reorder_request'),
    path('portal/change-password/',              views.portal_change_password, name='portal_change_password'),

    # ── Staff ──────────────────────────────────────────
    path('staff/',                               views.staff_dashboard,       name='staff_dashboard'),
    path('staff/change-password/',               views.staff_change_password, name='staff_change_password'),
    path('staff/customers/',                     views.staff_customer_list,   name='staff_customer_list'),
    path('staff/customers/add/',                 views.staff_customer_create, name='staff_customer_create'),
    path('staff/customers/<int:pk>/',            views.staff_customer_detail, name='staff_customer_detail'),
    path('staff/customers/<int:pk>/edit/',       views.staff_customer_edit,   name='staff_customer_edit'),
    path('staff/customers/<int:pk>/generate-account/',   views.staff_generate_account, name='staff_generate_account'),
    path('staff/customers/<int:pk>/manage-accounts/',    views.staff_manage_accounts,  name='staff_manage_accounts'),
    path('staff/scan-inbox/', views.scan_inbox, name='scan_inbox'),
    path('staff/ocr-api/', views.ocr_api_trigger, name='ocr_api_trigger'),
    path('staff/scan-inbox/done/<str:pair_id>/', views.scan_mark_done, name='scan_mark_done'),

    # PRODUCTS
    path('staff/products/',                      views.staff_product_list,    name='staff_product_list'),
    path('staff/products/add/',                  views.staff_product_create,  name='staff_product_create'),
    path('staff/products/<str:r_number>/',       views.staff_product_detail,  name='staff_product_detail'),
    path('staff/products/<str:r_number>/create-job/',    views.staff_job_create,          name='staff_job_create'),
    path('staff/products/<str:r_number>/edit/',  views.staff_product_edit,    name='staff_product_edit'),
    path('staff/products/<str:r_number>/print-setup/', views.staff_product_print_setup, name='staff_product_print_setup'),
    path('staff/products/<str:r_number>/print/', views.staff_product_print,   name='staff_product_print'),

    path('staff/jobs/',                          views.staff_job_list,        name='staff_job_list'),
    path('staff/jobs/<int:pk>/status/',          views.staff_job_update_status, name='staff_job_update_status'),
    path('staff/jobs/<int:pk>/print-setup/',           views.staff_job_print_setup,     name='staff_job_print_setup'),
    path('staff/jobs/<int:pk>/print/',           views.staff_job_print,       name='staff_job_print'),

]
