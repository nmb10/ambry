# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from ambry.library.search_backends.postgres_backend import PostgreSQLSearchBackend
from ambry.library import new_library
from test.test_base import PostgreSQLTestBase

from sqlalchemy.exc import ProgrammingError


class PostgreSQLBackendBaseTest(PostgreSQLTestBase):
    def setUp(self):
        super(PostgreSQLBackendBaseTest, self).setUp()

        # create test database
        rc = self.get_rc()
        self._real_test_database = rc.config['database']['test-database']
        rc.config['database']['test-database'] = self.dsn
        self.library = new_library(rc)
        self.backend = PostgreSQLSearchBackend(self.library)

    def tearDown(self):
        super(PostgreSQLBackendBaseTest, self).tearDown()

        # restore database config
        rc = self.get_rc()
        rc.config['database']['test-database'] = self._real_test_database


class PostgreSQLSearchBackendTest(PostgreSQLBackendBaseTest):

    # _or_join tests
    def test_joins_list_with_or(self):
        ret = self.backend._or_join(['term1', 'term2'])
        self.assertEqual(ret, 'term1 | term2')

    def test_returns_string_as_is(self):
        ret = self.backend._or_join('term1')
        self.assertEqual(ret, 'term1')

    # _and_join tests
    def test_joins_string_with_and(self):
        ret = self.backend._or_join(['term1', 'term2'])
        self.assertEqual(ret, 'term1 & term2')

    # _join_keywords tests
    def test_joins_keywords_with_and(self):
        ret = self.backend._join_keywords(['keyword1', 'keyword2'])
        self.assertEqual(ret, '(keyword1 & keyword2)')


class DatasetPostgreSQLIndexTest(PostgreSQLBackendBaseTest):

    def test_creates_dataset_index(self):
        with self.library.database._engine.connect() as conn:
            query = """
                SELECT * from dataset_index;
            """
            result = conn.execute(query).fetchall()
            self.assertEqual(result, [])

    # search() tests
    def test_returns_found_datasets(self):
        dataset1 = self.new_db_dataset(self.library.database, n=0)
        dataset2 = self.new_db_dataset(self.library.database, n=1)
        dataset1.config.metadata.about.title = 'title'
        dataset2.config.metadata.about.title = 'title'
        self.backend.dataset_index.index_one(dataset1)
        self.backend.dataset_index.index_one(dataset2)

        # testing.
        ret = self.backend.dataset_index.search('title')
        self.assertEqual(len(ret), 2)
        self.assertListEqual([dataset1.vid, dataset2.vid], [x.vid for x in ret])

    def test_returns_limited_datasets(self):
        for n in range(4):
            ds = self.new_db_dataset(self.library.database, n=n)
            ds.config.metadata.about.title = 'title'
            self.backend.dataset_index.index_one(ds)

        ret = self.backend.dataset_index.search('title')
        self.assertEqual(len(ret), 4)

        # testing
        ret = self.backend.dataset_index.search('title', limit=2)
        self.assertEqual(len(ret), 2)

    # reset tests
    def test_drops_dataset_index_table(self):
        self.backend.dataset_index.reset()
        with self.assertRaises(ProgrammingError):
            self.backend.library.database._engine.execute('SELECT * FROM dataset_index;')

    # is_indexed tests
    def test_returns_true_if_dataset_is_indexed(self):
        ds = self.new_db_dataset(self.library.database)
        self.backend.dataset_index.index_one(ds)
        ret = self.backend.dataset_index.is_indexed(ds)
        self.assertTrue(ret)

    def test_returns_false_if_dataset_is_not_indexed(self):
        ds = self.new_db_dataset(self.library.database)
        ret = self.backend.dataset_index.is_indexed(ds)
        self.assertFalse(ret)

    # all() tests
    def test_returns_list_with_all_indexed_datasets(self):
        ds1 = self.new_db_dataset(self.library.database, n=0)
        ds2 = self.new_db_dataset(self.library.database, n=1)
        self.backend.dataset_index.index_one(ds1)
        self.backend.dataset_index.index_one(ds2)
        ret = self.backend.dataset_index.all()
        self.assertEquals(len(ret), 2)

    # _make_query_from_terms tests
    def test_extends_query_with_limit(self):
        query, query_params = self.backend.dataset_index._make_query_from_terms('term1', limit=10)
        self.assertIn('LIMIT :limit', str(query))
        self.assertIn('limit', query_params)
        self.assertEqual(query_params['limit'], 10)

    # _delete tests
    def test_deletes_given_dataset_from_index(self):
        ds1 = self.new_db_dataset(self.library.database, n=0)
        self.backend.dataset_index.index_one(ds1)

        # was it really added?
        ret = self.backend.dataset_index.all()
        self.assertEquals(len(ret), 1)

        # delete and test
        self.backend.dataset_index._delete(vid=ds1.vid)
        ret = self.backend.dataset_index.all()
        self.assertEquals(len(ret), 0)
