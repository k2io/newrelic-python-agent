import webtest

from flask import Flask
from flask import Response
from flask_compress import Compress

from newrelic.agent import get_browser_timing_header, get_browser_timing_footer

application = Flask(__name__)

application.debug = True

compress = Compress()
compress.init_app(application)

@application.route('/compress')
def index_page():
    return '<body>' + 500*'X' + '</body>'

@application.route('/html_insertion')
def html_insertion():
    return  ('<!DOCTYPE html><html><head>Some header</head>'
            '<body><h1>My First Heading</h1><p>My first paragraph.</p>'
            '</body></html>')

@application.route('/html_insertion_manual')
def html_insertion_manual():
    header = get_browser_timing_header()
    footer = get_browser_timing_footer()

    header = get_browser_timing_header()
    footer = get_browser_timing_footer()

    assert header == ''
    assert footer == ''

    return ('<!DOCTYPE html><html><head>Some header</head>'
            '<body><h1>My First Heading</h1><p>My first paragraph.</p>'
            '</body></html>')

@application.route('/html_insertion_unnamed_attachment_header')
def html_insertion_unnamed_attachment_header():
    response = Response(response='<!DOCTYPE html><html><head>Some header</head>'
            '<body><h1>My First Heading</h1><p>My first paragraph.</p>'
            '</body></html>')
    response.headers.add('Content-Disposition',
                'attachment')
    return response

@application.route('/html_insertion_named_attachment_header')
def html_insertion_named_attachment_header():
    response = Response(response='<!DOCTYPE html><html><head>Some header</head>'
            '<body><h1>My First Heading</h1><p>My first paragraph.</p>'
            '</body></html>')
    response.headers.add('Content-Disposition',
                'attachment; filename="X"')
    return response

_test_application = webtest.TestApp(application)