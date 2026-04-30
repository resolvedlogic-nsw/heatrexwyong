import os
from pathlib import Path
from django.contrib.messages import constants as messages

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-81fdjy8$8$iv&byr*j2bag!*w&6ycfdsco96u(19-y$oa$1$r_"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['heatrexwyong.pythonanywhere.com']


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "products",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "heatrex_core.urls"

# In settings.py (optional but cleaner)
SCAN_INBOX_DIR = os.path.join(BASE_DIR, 'media', 'scan_inbox')
SCAN_COMPLETED_DIR = os.path.join(BASE_DIR, 'media', 'completed_scans')


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        'DIRS': [BASE_DIR / 'templates'],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "heatrex_core.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


MESSAGE_TAGS = {
    messages.ERROR:   'error',
    messages.SUCCESS: 'success',
    messages.INFO:    'info',
    messages.WARNING: 'info',
}


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
#    {
#        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
#    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Session security — expire browser sessions on close
SESSION_EXPIRE_AT_BROWSER_CLOSE = False   # set True if you want stricter auth
SESSION_COOKIE_AGE = 60 * 60 * 8          # 8 hours

# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = '/home/heatrexwyong/heatrex/static'
MEDIA_URL = '/media/'
MEDIA_ROOT = '/home/heatrexwyong/heatrex/media'

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL          = '/login/'
LOGIN_REDIRECT_URL = '/portal/'     # fallback if no ?next= param
LOGOUT_REDIRECT_URL = '/login/'

# ── EMAIL SERVER CONFIGURATION ──
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'heatrexwyong@gmail.com'
EMAIL_HOST_PASSWORD = 'puppwoqzdcejjyqi'
DEFAULT_FROM_EMAIL = 'Heatrex Portal <heatrexwyong@gmail.com>'

# Tell Django to use Google Cloud for uploaded/scanned media
DEFAULT_FILE_STORAGE = 'storages.backends.gcloud.GoogleCloudStorage'

# Put the bucket name you created in Step 1 here:
GS_BUCKET_NAME = 'heatrex-card-archives'

# Never overwrite an old scan if two files happen to have the exact same name
GS_FILE_OVERWRITE = False

# Tell Google to generate temporary secure links that expire in 10 minutes
# (Keeps your customer data highly secure)
GS_DEFAULT_ACL = None