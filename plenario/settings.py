from os import environ
get = environ.get

SECRET_KEY = get('SECRET_KEY', 'abcdefghijklmnop')
PLENARIO_SENTRY_URL = get('PLENARIO_SENTRY_URL', None)
CELERY_SENTRY_URL = get('CELERY_SENTRY_URL', None)
DATA_DIR = '/tmp'

DB_USER = get('DB_USER', 'plenario')
DB_PASSWORD = get('DB_PASSWORD', 'plenario22')
DB_HOST = get('DB_HOST', 'aot-metadata-test2.c6r1g0x3odle.us-east-1.rds.amazonaws.com')
DB_PORT = get('DB_PORT', '5432')
DB_NAME = get('DB_NAME', 'aotMeta')

RS_USER = get('RS_USER', 'plenario')
RS_PASSWORD = get('RS_PASSWORD', 'Plenario22')
RS_HOST = get('RS_HOST', 'aot-test.cgsdruid1put.us-east-1.redshift.amazonaws.com')
RS_PORT = get('RS_PORT', '5439')
RS_NAME = get('RS_NAME', 'dev')

DATABASE_CONN = 'postgresql://{}:{}@{}:{}/{}'.\
    format(DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME)
REDSHIFT_CONN = 'redshift+psycopg2://{}:{}@{}:{}/{}'.\
    format(RS_USER, RS_PASSWORD, RS_HOST, RS_PORT, RS_NAME)

REDIS_HOST = get('REDIS_HOST', 'localhost')

# See: https://pythonhosted.org/Flask-Cache/#configuring-flask-cache
# for config options
CACHE_CONFIG = {
    'CACHE_TYPE': 'redis',
    'CACHE_REDIS_HOST': REDIS_HOST,
    'CACHE_KEY_PREFIX': get('CACHE_KEY_PREFIX', 'plenario_app')
}

# Load a default admin
DEFAULT_USER = {
    'name': get('DEFAULT_USER_NAME'),
    'email': get('DEFAULT_USER_EMAIL'),
    'password': get('DEFAULT_USER_PASSWORD')
}

AWS_ACCESS_KEY = get('AWS_ACCESS_KEY', 'AKIAJFBRMR24ATJJKXAA')
AWS_SECRET_KEY = get('AWS_SECRET_KEY', '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')
S3_BUCKET = get('S3_BUCKET', '')
AWS_REGION_NAME = get('AWS_REGION_NAME', 'us-east-1')

# Email address for notifying site administrators
# Expect comma-delimited list of emails.
email_list = get('ADMIN_EMAILS')
if email_list:
    ADMIN_EMAILS = email_list.split(',')
else:
    ADMIN_EMAILS = []

# For emailing users. ('MAIL_USERNAME' is an email address.)
MAIL_SERVER = get('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = 587
MAIL_USE_TLS = True
MAIL_DISPLAY_NAME = 'Plenar.io Team'
MAIL_USERNAME = get('MAIL_USERNAME', '')
MAIL_PASSWORD = get('MAIL_PASSWORD', '')

# Toggle maintenence mode
MAINTENANCE = False
