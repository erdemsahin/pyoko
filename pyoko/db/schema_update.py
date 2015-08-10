# -*-  coding: utf-8 -*-
"""
"""

# Copyright (C) 2015 ZetaOps Inc.
#
# This file is licensed under the GNU General Public License v3
# (GPLv3).  See LICENSE.txt for details.

import codecs
from random import randint
import threading
import time
from pyoko.conf import settings
from pyoko.db.connection import client
import os, inspect
from pyoko.lib.utils import un_camel, random_word


class SchemaUpdater(object):
    """
    traverses trough all models, collects fields marked for index or store in solr
    then creates a solr schema for these fields.
    """

    FIELD_TEMPLATE = '<field type="{type}" name="{name}"  indexed="{index}" ' \
                     'stored="{store}" multiValued="{multi}" />'

    def __init__(self, registry, bucket_names, silent=None):
        self.report = []
        self.registry = registry
        self.client = client
        self.silent = silent
        self.bucket_names = [b.lower() for b in bucket_names.split(',')]
        self.t1 = 0.0  # start time

    def run(self):
        self.t1 = time.time()
        apply_threads = []
        for model in self.registry.get_base_models():
            if self.bucket_names[
                0] == 'all' or model.__name__.lower() in self.bucket_names:
                ins = model()
                fields = self.get_schema_fields(ins._collect_index_fields())
                new_schema = self.compile_schema(fields)

                apply_threads.append(threading.Thread(target=self.apply_schema,
                                                      args=(
                                                          self.client,
                                                          new_schema,
                                                          model, self.silent)))
        if not self.silent:
            print(
            "Schema creation started for %s model" % len(apply_threads))
        for t in apply_threads:
            t.start()
        for t in apply_threads:
            t.join()
        if apply_threads:
            self.report = "Schema and index definitions successfully " \
                          "applied for the models listed above."


    def create_report(self):
        """
        creates a text report for the human user
        :return: str
        """

        if self.report:
            self.report += "\n Operation took %s secs" % round(
                time.time() - self.t1)
        else:
            self.report = "Operation failed: %s \n" % self.report
        return self.report

    @classmethod
    def get_schema_fields(cls, fields):
        """

        :param list[(,)] fields: field props tupple list
        :rtype: list[str]
        :return: schema fields list
        """
        return [cls.FIELD_TEMPLATE.format(name=name,
                                          type=field_type,
                                          index=str(index).lower(),
                                          store=str(store).lower(),
                                          multi=str(multi).lower())
                for name, field_type, index, store, multi in fields]

    def compile_schema(self, fields):
        """
        joins schema fields with base solr schema

        :param list[str] fields: field list
        :return: compiled schema
        :rtype: byte
        """
        path = os.path.dirname(
            os.path.abspath(inspect.getfile(inspect.currentframe())))
        with codecs.open("%s/solr_schema_template.xml" % path, 'r',
                         'utf-8') as fh:
            schema_template = fh.read()
        return schema_template.format('\n'.join(fields)).encode('utf-8')

    @staticmethod
    def apply_schema(client, new_schema, model, silent=False):
        """
        riak doesn't support schema/index updates ( http://git.io/vLOTS )

        as a workaround, we create a temporary index,
        attach it to the bucket, delete the old index/schema,
        re-create the index with new schema, assign it to bucket,
        then delete the temporary index.

        :param byte new_schema: compiled schema
        :param str bucket_name: name of schema, index and bucket.
        :return: True or False
        :rtype: bool
        """
        bucket_name = model._get_bucket_name()
        bucket_type = client.bucket_type(settings.DEFAULT_BUCKET_TYPE)
        bucket = bucket_type.bucket(bucket_name)
        n_val = bucket_type.get_property('n_val')
        # delete stale indexes
        # inuse_indexes = [b.get_properties().get('search_index') for b in
        #                  bucket_type.get_buckets()]
        # stale_indexes = [si['name'] for si in self.client.list_search_indexes()
        #                     if si['name'] not in inuse_indexes]
        # for stale_index in stale_indexes:
        #     self.client.delete_search_index(stale_index)

        new_index_name = "%s_%s" % (bucket_name, randint(1000, 9999999))
        client.create_search_schema(new_index_name, new_schema)
        client.create_search_index(new_index_name, new_index_name, n_val)
        bucket.set_property('search_index', new_index_name)
        settings.update_index(bucket_name, new_index_name)
        if not silent:
            print("+ %s " % model.__name__)