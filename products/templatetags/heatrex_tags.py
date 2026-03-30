from django import template
from products.models import Job

register = template.Library()

@register.filter
def status_badge_class(status):
    mapping = {
        'PENDING':    'pending',
        'CONFIRMED':  'confirmed',
        'IN_PROD':    'in-prod',
        'QC':         'qc',
        'DISPATCHED': 'dispatched',
        'INVOICED':   'invoiced',
    }
    return mapping.get(status, 'pending')

@register.simple_tag
def job_status_choices():
    return Job.STATUS_CHOICES

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)
