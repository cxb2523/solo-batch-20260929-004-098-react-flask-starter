from flask import (
    Blueprint,
    jsonify
)

from src import audit
from src.models import Counter

import datetime


blueprint = Blueprint('counter', __name__)

RESPONSE_TEMPLATE = 'Flask server running on port 8080. Pinged {count} {times}, \
most recently on {date}.'


def _not_ready_response():
    """503 instead of an empty/errored table while migrations are pending."""
    if audit.migrations_ready():
        return None
    return jsonify(
        error='database migrations pending',
        status='unavailable'), 503


@blueprint.route('/')
def index():
    response = 'Flask server'
    return jsonify(response=response)


@blueprint.route('/api/v1/reset/')
def reset():
    not_ready = _not_ready_response()
    if not_ready:
        return not_ready
    counter = Counter.get_create(label='Test')
    counter.reset()
    return jsonify(response=counter.count)


@blueprint.route('/api/v1/')
def api():
    not_ready = _not_ready_response()
    if not_ready:
        return not_ready
    counter = Counter.get_create(label='Test')
    counter.increment()
    date = datetime.datetime.now()
    dateStr = date.strftime('%c')
    times = 'time' if counter.count == 1 else 'times'
    response = RESPONSE_TEMPLATE.format(
        count=counter.count,
        times=times,
        date=dateStr)
    return jsonify(response=response)
