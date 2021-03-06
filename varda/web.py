# -*- coding: utf-8 -*-
"""
Serve Aulë static files.

This is used if the configuration setting `AULE_LOCAL_PATH` is set. The
directory specified by that setting is then served under the prefix specified
by the `AULE_URL_PREFIX` configuration setting.

This is useful during local development, since Varda and Aulë must share the
same domain by the browser's same-origin policy.

.. moduleauthor:: Martijn Vermaat <martijn@vermaat.name>

.. Licensed under the MIT license, see the LICENSE file.
"""


import os.path

from flask import Blueprint, current_app, send_from_directory


web = Blueprint('web', __name__)


@web.route('/')
@web.route('/<path:filename>')
def serve(filename=None):
    path = current_app.config['AULE_LOCAL_PATH']
    if not filename or not os.path.isfile(os.path.join(path, filename)):
        filename = 'index.html'
    send_kwargs = {}
    if current_app.debug:
        send_kwargs.update(add_etags=False, cache_timeout=1)
    return send_from_directory(path, filename, **send_kwargs)
