import webtest

from flask import Flask, render_template_string, render_template, abort

application = Flask(__name__)

@application.route('/index')
def index_page():
    return 'INDEX RESPONSE'

@application.route('/error')
def error_page():
    raise RuntimeError('RUNTIME ERROR')

@application.route('/abort_404')
def abort_404_page():
    abort(404)

@application.route('/template_string')
def template_string():
    return render_template_string('<body><p>INDEX RESPONSE</p></body>')

@application.route('/template_not_found')
def template_not_found():
    return render_template('not_found')

_test_application = webtest.TestApp(application)
