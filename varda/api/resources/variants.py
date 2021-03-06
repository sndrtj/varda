"""
REST API variants resource.

.. moduleauthor:: Martijn Vermaat <martijn@vermaat.name>

.. Licensed under the MIT license, see the LICENSE file.
"""


from flask import g, jsonify

from ...models import Observation, Sample, Variation
from ...region_binning import all_bins
from ...utils import (calculate_frequency, normalize_region, normalize_variant,
                      ReferenceMismatch)
from ..errors import ValidationError
from ..security import has_role, owns_sample, public_sample, true
from .base import Resource
from .samples import SamplesResource


def _satisfy_lookup(conditions):
    sample_selected, is_admin, is_annotator, owns_sample, public_sample\
        = conditions
    if is_admin:
        return True
    if sample_selected:
        return owns_sample or public_sample
    return is_annotator


class VariantsResource(Resource):
    """
    Variant resources model genomic variants with their observed frequencies.

    .. note:: The implementation of this resource is still in flux and it is
       therefore not documented.
    """
    instance_name = 'variant'
    instance_type = 'variant'

    views = ['list', 'get', 'add']

    orderable = ['chromosome', 'position']

    default_order = [('chromosome', 'asc'),
                     ('position', 'asc'),
                     ('reference', 'asc'),
                     ('observed', 'asc'),
                     ('id', 'asc')]

    list_ensure_conditions = [true('sample'), has_role('admin'),
                              has_role('annotator'), owns_sample,
                              public_sample]
    list_ensure_options = {'satisfy': _satisfy_lookup}
    list_schema = {'region': {'type': 'dict',
                              'schema': {'chromosome': {'type': 'string', 'required': True, 'maxlength': 30},
                                         'begin': {'type': 'integer', 'required': True},
                                         'end': {'type': 'integer', 'required': True}},
                              'required': True},
                   'sample': {'type': 'sample'}}

    get_ensure_conditions = [true('sample'), has_role('admin'),
                             has_role('annotator'), owns_sample,
                             public_sample]
    get_ensure_options = {'satisfy': _satisfy_lookup}
    get_schema = {'sample': {'type': 'sample'}}

    add_ensure_conditions = []
    add_schema = {'chromosome': {'type': 'string', 'required': True, 'maxlength': 30},
                  'position': {'type': 'integer', 'required': True},
                  'reference': {'type': 'string', 'maxlength': 200},
                  'observed': {'type': 'string', 'maxlength': 200}}

    key_type = 'string'

    @classmethod
    def instance_key(cls, variant):
        return '%s:%d%s>%s' % variant

    @classmethod
    def serialize(cls, variant, sample=None):
        """
        A variant is represented as an object with the following fields:

        **uri** (`uri`)
          URI for this resource.
        """
        chromosome, position, reference, observed = variant

        coverage, frequency = calculate_frequency(
            chromosome, position, reference, observed, sample=sample)

        if sample is not None:
            sample_uri = SamplesResource.instance_uri(sample)
        else:
            sample_uri = None

        return {'uri': cls.instance_uri(variant),
                'sample_uri': sample_uri,
                'chromosome': chromosome,
                'position': position,
                'reference': reference,
                'observed': observed,
                'coverage': coverage,
                'frequency': sum(frequency.values()),
                'frequency_het': frequency['heterozygous'],
                'frequency_hom': frequency['homozygous']}

    @classmethod
    def list_view(cls, begin, count, region, sample=None, order=None):
        """
        Returns a collection of variants in the `variant_collection` field.
        """
        # Todo: Document that `begin` and `end` are 1-based and inclusive. Or,
        #     perhaps we should change that to conform to BED track regions.
        try:
            chromosome, begin_position, end_position = normalize_region(
                region['chromosome'], region['begin'], region['end'])
        except ReferenceMismatch as e:
            raise ValidationError(str(e))

        bins = all_bins(begin_position, end_position)
        observations = Observation.query.filter(
            Observation.chromosome == chromosome,
            Observation.position >= begin_position,
            Observation.position <= end_position,
            Observation.bin.in_(bins))

        # Filter by sample, or by samples with coverage profile otherwise.
        if sample:
            observations = observations \
                .join(Variation).filter_by(sample=sample)
        else:
            observations = observations \
                .join(Variation).join(Sample).filter_by(active=True,
                                                        coverage_profile=True)

        observations = observations.distinct(Observation.chromosome,
                                             Observation.position,
                                             Observation.reference,
                                             Observation.observed)

        observations = observations.order_by(*[getattr(getattr(Observation, f), d)()
                                               for f, d in cls.get_order(order)])

        items = [cls.serialize((o.chromosome, o.position, o.reference, o.observed),
                               sample=sample)
                 for o in observations.limit(count).offset(begin)]
        return (observations.count(),
                jsonify(variant_collection={'uri': cls.collection_uri(),
                                            'items': items}))

    @classmethod
    def get_view(cls, variant, sample=None):
        """
        Returns the variant representation in the `variant` field.
        """
        return jsonify(variant=cls.serialize(variant, sample=sample))

    @classmethod
    def add_view(cls, chromosome, position, reference='', observed=''):
        """
        Adds a variant resource.
        """
        # Todo: Also support HGVS input.
        try:
            variant = normalize_variant(chromosome, position, reference, observed)
        except ReferenceMismatch as e:
            raise ValidationError(str(e))
        uri = cls.instance_uri(variant)
        # Note: It doesn't really make sense to calculate global frequencies
        #     here (the client might only be interested in frequencies for
        #     some specific sample), so we only return the URI instead of a
        #     full serialization.
        response = jsonify(variant={'uri': uri})
        response.location = uri
        return response, 201
