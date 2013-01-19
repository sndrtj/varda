"""
REST API resources.

.. moduleauthor:: Martijn Vermaat <martijn@vermaat.name>

.. Licensed under the MIT license, see the LICENSE file.
"""


import re

from flask import (abort, current_app, g, jsonify, redirect, request,
                   send_from_directory, url_for)

from .. import db
from ..models import (Annotation, Coverage, DataSource, DATA_SOURCE_FILETYPES,
                      InvalidDataSource, Observation, Sample, User,
                      USER_ROLES, Variation)
from .. import tasks
from .data import data
from .errors import ActivationFailure, ValidationError
from .security import (ensure, is_user, has_role, owns_annotation,
                       owns_coverage, owns_data_source, owns_sample,
                       owns_variation, true, require_user)
from .serialize import serialize
from .utils import collection, user_by_login


class Resource(object):
    model = None
    instance_name = None
    instance_type = None

    views = ['list', 'get', 'add', 'edit']

    embeddable = []
    filterable = {}

    list_ensure_conditions = [has_role('admin')]
    list_ensure_options = {}
    list_schema = {}

    get_ensure_conditions = [has_role('admin')]
    get_ensure_options = {}
    get_schema = {}

    add_ensure_conditions = [has_role('admin')]
    add_ensure_options = {}
    add_schema = {}

    edit_ensure_conditions = [has_role('admin')]
    edit_ensure_options = {}
    edit_schema = {}

    def __new__(cls, *args, **kwargs):
        cls.list_rule = '/'
        cls.get_rule = '/<int:%s>' % cls.instance_name
        cls.add_rule = '/'
        cls.edit_rule = '/<int:%s>' % cls.instance_name

        id_schema = {cls.instance_name: {'type': cls.instance_type}}
        cls.get_schema.update(id_schema)
        cls.edit_schema.update(id_schema)
        if cls.embeddable:
            embed_schema = {'embed': {'type': 'list', 'allowed': cls.embeddable}}
            cls.list_schema.update(embed_schema)
            cls.get_schema.update(embed_schema)
        if cls.filterable:
            filter_schema = {k: {'type': v} for k, v in cls.filterable.items()}
            cls.list_schema.update(filter_schema)
        return object.__new__(cls, *args, **kwargs)

    def __init__(self, blueprint, url_prefix=None):
        self.blueprint = blueprint
        self.url_prefix = url_prefix
        self.register_views()

    def register_views(self):
        if 'list' in self.views:
            self.register_view('list', wrapper=collection)
        if 'get' in self.views:
            self.register_view('get')
        if 'add' in self.views:
            self.register_view('add', methods=['POST'])
        if 'edit' in self.views:
            self.register_view('edit', methods=['PATCH'])

    def register_view(self, endpoint, wrapper=None, **kwargs):
        if wrapper is None:
            wrapper = lambda f: f

        @require_user
        @data(**getattr(self, '%s_schema' % endpoint))
        @ensure(*getattr(self, '%s_ensure_conditions' % endpoint),
                **getattr(self, '%s_ensure_options' % endpoint))
        @wrapper
        def view_func(*args, **kwargs):
            return getattr(self, '%s_view' % endpoint)(*args, **kwargs)

        # Todo: Work out API docs.
        view_func.__doc__ = 'Documentation for view: %s' % endpoint

        self.blueprint.add_url_rule('%s%s' % (self.url_prefix or '/', getattr(self, '%s_rule' % endpoint)),
                                    '%s_%s' % (self.instance_type, endpoint),
                                    view_func,
                                    **kwargs)

    def list_view(self, begin, count, embed=None, **filter):
        resources = self.model.query
        if filter:
            resources = resources.filter_by(**filter)
        return (resources.count(),
                jsonify(resources=[serialize(r, embed=embed) for r in
                                   resources.limit(count).offset(begin)]))

    def get_view(self, embed=None, **kwargs):
        resource = kwargs.get(self.instance_name)
        return jsonify({self.instance_name: serialize(resource, embed=embed)})

    def add_view(self, *args, **kwargs):
        # Todo: Way to provide default values?
        resource = self.model(**kwargs)
        db.session.add(resource)
        db.session.commit()
        current_app.logger.info('Added %s: %r', self.instance_name, resource)
        uri = url_for('.%s_get' % self.instance_type, **{self.instance_name: resource.id})
        response = jsonify({'%s_uri' % self.instance_name: uri})
        response.location = uri
        return response, 201

    def edit_view(self, *args, **kwargs):
        resource = kwargs.pop(self.instance_name)
        for field, value in kwargs.items():
            setattr(resource, field, value)
        db.session.commit()
        current_app.logger.info('Updated %s: %r', self.instance_name, resource)
        return jsonify({self.instance_name: serialize(resource)})


class TaskedResource(Resource):
    task = None

    def get_view(self, embed=None, **kwargs):
        resource = kwargs.get(self.instance_name)
        progress = None
        if not resource.task_done and resource.task_uuid:
            result = self.task.AsyncResult(resource.task_uuid)
            try:
                # This re-raises a possible TaskError, handled by error_task_error
                # above.
                # Todo: Re-raising doesn't seem to work at the moment...
                result.get(timeout=3)
            except celery.exceptions.TimeoutError:
                pass
            if result.state == 'PROGRESS':
                progress = result.info['percentage']
        return jsonify({self.instance_name: serialize(resource, embed=embed), 'progress': progress})

    def add_view(self, *args, **kwargs):
        resource = self.model(**kwargs)
        db.session.add(resource)
        db.session.commit()
        current_app.logger.info('Added %s: %r', self.instance_name, resource)
        result = self.task.delay(resource.id)
        current_app.logger.info('Called task: %s(%d) %s', self.task.__name__, resource.id, result.task_id)
        uri = url_for('.%s_get' % self.instance_type, **{self.instance_name: resource.id})
        response = jsonify({'%s_uri' % self.instance_name: uri})
        response.location = uri
        return response, 202


class UsersResource(Resource):
    model = User
    instance_name = 'user'
    instance_type = 'user'

    get_ensure_conditions = [has_role('admin'), is_user]
    get_ensure_options = {'satisfy': any}

    add_schema = {'login': {'type': 'string', 'minlength': 3, 'maxlength': 40,
                            'safe': True, 'required': True},
                  'name': {'type': 'string'},
                  'password': {'type': 'string', 'required': True},
                  'roles': {'type': 'list', 'allowed': USER_ROLES}}

    edit_schema = {'name': {'type': 'string'},
                   'password': {'type': 'string'},
                   'roles': {'type': 'list', 'allowed': USER_ROLES}}

    def add_view(self, **kwargs):
        login = kwargs.get('login')
        kwargs['name'] = kwargs.get('name', login)
        if User.query.filter_by(login=login).first() is not None:
            raise ValidationError('User login is not unique')
        return super(UsersResource, self).add_view(**kwargs)


class SamplesResource(Resource):
    model = Sample
    instance_name = 'sample'
    instance_type = 'sample'

    filterable = {'public': 'boolean',
                  'user': 'user'}

    list_ensure_conditions = [has_role('admin'), is_user, true('public')]
    list_ensure_options = {'satisfy': any}

    get_ensure_conditions = [has_role('admin'), owns_sample]
    get_ensure_options = {'satisfy': any}

    add_ensure_conditions = [has_role('admin'), has_role('importer')]
    add_ensure_options = {'satisfy': any}
    add_schema = {'name': {'type': 'string', 'required': True},
                  'pool_size': {'type': 'integer'},
                  'coverage_profile': {'type': 'boolean'},
                  'public': {'type': 'boolean'}}

    edit_ensure_conditions = [has_role('admin'), owns_sample]
    edit_ensure_options = {'satisfy': any}
    edit_schema = {'active': {'type': 'boolean'},
                   'name': {'type': 'string'},
                   'pool_size': {'type': 'integer'},
                   'coverage_profile': {'type': 'boolean'},
                   'public': {'type': 'boolean'}}

    def add_view(self, **kwargs):
        kwargs['user'] = g.user
        return super(SamplesResource, self).add_view(**kwargs)

    def edit_view(self, *args, **kwargs):
        if kwargs.get('active'):
            # Todo: Checks, e.g. if there are expected imported data sources
            # and no imports running at the moment. Also, number of coverage
            # tracks should be 0 or equal to pool size.
            #raise ActivationFailure('reason', 'This is the reason')
            pass
        else:
            # Todo: Always, even on name change?
            kwargs['active'] = False
        return super(SamplesResource, self).edit_view(**kwargs)


class VariationsResource(TaskedResource):
    model = Variation
    instance_name = 'variation'
    instance_type = 'variation'

    task = tasks.import_variation

    views = ['list', 'get', 'add']

    embeddable = ['data_source', 'sample']
    filterable = {'sample': 'sample'}

    list_ensure_conditions = [has_role('admin'), owns_sample]
    list_ensure_options = {'satisfy': any}

    get_ensure_conditions = [has_role('admin'), owns_variation]
    get_ensure_options = {'satisfy': any}

    add_ensure_conditions = [has_role('admin'), owns_sample]
    add_ensure_options = {'satisfy': any}
    add_schema = {'sample': {'type': 'sample', 'required': True},
                  'data_source': {'type': 'data_source', 'required': True}}


class CoveragesResource(TaskedResource):
    model = Coverage
    instance_name = 'coverage'
    instance_type = 'coverage'

    task = tasks.import_coverage

    views = ['list', 'get', 'add']

    embeddable = ['data_source', 'sample']
    filterable = {'sample': 'sample'}

    list_ensure_conditions = [has_role('admin'), owns_sample]
    list_ensure_options = {'satisfy': any}

    get_ensure_conditions = [has_role('admin'), owns_coverage]
    get_ensure_options = {'satisfy': any}

    add_ensure_conditions = [has_role('admin'), owns_sample]
    add_ensure_options = {'satisfy': any}
    add_schema = {'sample': {'type': 'sample', 'required': True},
                  'data_source': {'type': 'data_source', 'required': True}}


class DataSourcesResource(Resource):
    model = DataSource
    instance_name = 'data_source'
    instance_type = 'data_source'

    views = ['list', 'get', 'add', 'edit', 'data']

    filterable = {'user': 'user'}

    list_ensure_conditions = [has_role('admin'), is_user]
    list_ensure_options = {'satisfy': any}

    get_ensure_conditions = [has_role('admin'), owns_data_source]
    get_ensure_options = {'satisfy': any}

    add_ensure_conditions = []
    add_schema = {'name': {'type': 'string', 'required': True},
                  'filetype': {'type': 'string', 'allowed': DATA_SOURCE_FILETYPES,
                               'required': True},
                  'gzipped': {'type': 'boolean'},
                  'local_path': {'type': 'string'}}

    edit_schema = {'name': {'type': 'string'}}

    data_rule = '/<int:data_source>/data'
    data_ensure_conditions = [has_role('admin'), owns_data_source]
    data_ensure_options = {'satisfy': any}
    data_schema = {'data_source': {'type': 'data_source'}}

    def register_views(self):
        super(DataSourcesResource, self).register_views()
        if 'data' in self.views:
            self.register_view('data')

    def add_view(self, **kwargs):
        # Todo: If files['data'] is missing (or non-existent file?), we crash with
        #     a data_source_not_cached error.
        # Todo: Sandbox local_path (per user).
        # Todo: Option to upload the actual data later at the /data_source/XX/data
        #     endpoint, symmetrical to the GET request.
        kwargs.update(user=g.user, upload=request.files.get('data'))
        return super(DataSourcesResource, self).add_view(**kwargs)

    def data_view(self, data_source):
        return send_from_directory(current_app.config['FILES_DIR'],
                                   data_source.filename,
                                   mimetype='application/x-gzip')


class AnnotationsResource(TaskedResource):
    model = Annotation
    instance_name = 'annotation'
    instance_type = 'annotation'

    task = tasks.write_annotation

    views = ['list', 'get', 'add']

    filterable = {'data_source': 'data_source'}

    list_ensure_conditions = [has_role('admin'), owns_data_source]
    list_ensure_options = {'satisfy': any}

    get_ensure_conditions = [has_role('admin'), owns_annotation]
    get_ensure_options = {'satisfy': any}

    add_ensure_conditions = [has_role('admin'), owns_data_source,
                             has_role('annotator'), has_role('trader')]
    add_ensure_options = {'satisfy': lambda conditions: next(conditions) or (next(conditions) and any(conditions))}
    add_schema = {'data_source': {'type': 'data_source', 'required': True},
                  'global_frequencies': {'type': 'boolean'},
                  'exclude_samples': {'type': 'list', 'schema': {'type': 'sample'}},
                  'include_samples': {'type': 'list',
                                      'schema': {'type': 'list',
                                                 'items': [{'type': 'string'},
                                                           {'type': 'sample'}]}}}

    def add_view(self, data_source, global_frequencies=False,
                 exclude_samples=None, include_samples=None):
        # Todo: Check if data source is a VCF file.
        # Todo: The `include_samples` might be better structured as a list of
        #     objects, e.g. ``[{label: GoNL, sample: ...}, {label: 1KG, sample: ...}]``.
        # The `satisfy` keyword argument used here in the `ensure` decorator means
        # that we ensure at least one of:
        # - admin
        # - owns_data_source AND annotator
        # - owns_data_source AND trader
        exclude_samples = exclude_samples or []

        # Todo: Perhaps a better name would be `local_frequencies` instead of
        #     `include_sample_ids`, to contrast with the `global_frequencies`
        #     flag.
        include_samples = dict(include_samples or [])

        if not all(re.match('[0-9A-Z]+', label) for label in include_samples):
            raise ValidationError('Labels for inluded samples must contain only'
                                  ' uppercase alphanumeric characters')

        for sample in include_samples.values():
            if not (sample.public or
                    sample.user is g.user or
                    'admin' in g.user.roles):
                # Todo: Meaningful error message.
                abort(400)

        if 'admin' not in g.user.roles and 'annotator' not in g.user.roles:
            # This is a trader, so check if the data source has been imported in
            # an active sample.
            # Todo: Anyone should be able to annotate against the public samples.
            if not data_source.variations.join(Sample).filter_by(active=True).count():
                raise InvalidDataSource('inactive_data_source', 'Data source '
                    'cannot be annotated unless it is imported in an active sample')

        annotated_data_source = DataSource(g.user,
                                           '%s (annotated)' % data_source.name,
                                           data_source.filetype,
                                           empty=True, gzipped=True)
        db.session.add(annotated_data_source)
        annotation = Annotation(data_source, annotated_data_source)
        db.session.add(annotation)
        db.session.commit()
        current_app.logger.info('Added data source: %r', annotated_data_source)
        current_app.logger.info('Added annotation: %r', annotation)

        result = tasks.write_annotation.delay(annotation.id,
                                              global_frequencies=global_frequencies,
                                              exclude_sample_ids=[sample.id for sample in exclude_samples],
                                              include_sample_ids={label: sample.id for label, sample in include_samples.items()})
        current_app.logger.info('Called task: write_annotation(%d) %s', annotation.id, result.task_id)
        uri = url_for('.annotation_get', annotation=annotation.id)
        response = jsonify(annotation_uri=uri)
        response.location = uri
        return response, 202
