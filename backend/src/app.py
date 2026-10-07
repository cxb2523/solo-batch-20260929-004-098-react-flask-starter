import os

from flask import Flask, jsonify
from flask_migrate import Migrate

from src import audit, routes, utils as u
from src.models import db
from src.settings import Settings as S
from src.tasks import make_celery


class Application:

    @classmethod
    def boot(cls):
        """Begin the application"""
        app = cls()
        app.run()
        return app

    def __init__(self):
        self.init_flask()
        self.init_routes()

    def init_flask(self):
        # Init Flask
        self.flask_app = Flask(__name__, static_url_path='/static')
        self.flask_app.config.from_object(S)

        # Register the public routes
        for blueprint in [routes.counter, routes.localstack]:
            self.flask_app.register_blueprint(blueprint)

        # Init Celery
        self.celery = make_celery(self.flask_app)

        # Init Flask-SQLAlchemy
        self.db = db
        self.db.init_app(self.flask_app)

        # Init Flask-Migrate
        u.wait_for_service('postgres', 5432, timeout=30.0)
        self.migrate = Migrate(self.flask_app, self.db)

    def init_routes(self):

        @self.flask_app.route('/')
        def home(key):
            return 'home'

        @self.flask_app.route('/api/system/audit')
        def system_audit():
            # Single implementation lives in src.audit — do not re-resolve
            # anything here.
            report = audit.build_report(role=os.environ.get('APP_ROLE', 'web'))
            return jsonify(report)
