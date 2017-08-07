import codecs
import logging.config
from logging import getLogger

import yaml
from flask import Flask, render_template
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from raven.contrib.flask import Sentry

from plenario.models import bcrypt
from plenario.settings import DATABASE_CONN, PLENARIO_SENTRY_URL, REDSHIFT_CONN
from plenario.utils.helpers import slugify as slug

sentry = None
if PLENARIO_SENTRY_URL:
    sentry = Sentry(dsn=PLENARIO_SENTRY_URL)


# Set up the logger using parameters found in the 'log.yaml' file
with codecs.open('log.yaml', mode='r', encoding='utf8') as fh:
    config = yaml.load(fh)
logging.config.dictConfig(config)
logger = getLogger(__name__)


# Class used to manage sqlalchemy integration with a flask application.
db = SQLAlchemy()

# Specifies multiple binds, for each bind flask-sqlalchemy will maintain an
# engine to communicate with the database.
SQLALCHEMY_BINDS = {
    'postgres': DATABASE_CONN,
    'redshift': REDSHIFT_CONN
}

# Specifies the default database connection, this is the database used when no
# bind is specified. In this case it is postgresql.
SQLALCHEMY_DATABASE_URI = DATABASE_CONN

# Echoes the sql being executed by sqlalchemy to stdout. It's a nifty setting
# to enable when you're debugging things.
SQLALCHEMY_ECHO = True

# Tracking modifications are necessary for registering hooks on signals
# emitted by sqlalchemy models.
SQLALCHEMY_TRACK_MODIFICATIONS = True


def create_app():
    logger.info('beginning application setup')

    # API depends on the tables in the database to exist.
    # Don't import until we really need it to create the app
    # Since otherwise it may be called before init_db.py runs.
    from plenario.api.blueprints import api, cache

    # These other imports might eventually use API as well.
    # plenario.views does now. So we'll put them here like
    # API and not import them until they're really needed.
    from plenario.apiary.blueprints import apiary, apiary_bp
    from plenario.auth import auth, login_manager
    from plenario.views import views

    app = Flask(__name__)
    app.response_class = JsonResponse

    app.config.from_object('plenario.settings')
    app.config['SQLALCHEMY_BINDS'] = SQLALCHEMY_BINDS
    app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['JSON_SORT_KEYS'] = False
    app.url_map.strict_slashes = False
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    bcrypt.init_app(app)
    CORS(app)
    db.init_app(app)

    if sentry:
        sentry.init_app(app)
    app.register_blueprint(api)
    app.register_blueprint(views)
    app.register_blueprint(auth)
    cache.init_app(app)

    apiary.init_app(app)
    app.register_blueprint(apiary_bp)

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def page_not_found(e):
        db.session.rollback()
        return render_template('error.html'), 500

    @app.template_filter('slugify')
    def slugify(s):
        return slug(s)

    @app.template_filter('format_number')
    def reverse_filter(s):
        return '{:,}'.format(s)

    @app.template_filter('format_date_sort')
    def reverse_filter(s):
        if s:
            return s.strftime('%Y%m%d%H%M')
        else:
            return '0'

    @app.template_filter('has_description')
    def has_description(list_of_cols):
        try:
            # Any description attribute filled?
            return any([col['description'] for col in list_of_cols])
        except KeyError:
            # Is there even a description attribute?
            return False

    logger.info('application setup completed')
    return app


from plenario.response import JsonResponse
