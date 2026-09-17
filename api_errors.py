"""Return API failures as JSON without changing page responses or HTTP headers."""
from flask import current_app, request
from werkzeug.exceptions import HTTPException


def register_api_errors(app):
    @app.errorhandler(HTTPException)
    def api_http_error(error):
        if not request.path.startswith('/api/'):
            return error
        message = error.description
        if error.code >= 500:
            message = 'Сервер не смог выполнить запрос. Обновите данные перед повторной попыткой.'
        response = error.get_response()
        response.set_data(current_app.json.dumps({'error': message}))
        response.content_type = 'application/json'
        response.headers['Cache-Control'] = 'no-store'
        return response
