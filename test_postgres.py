"""Opt-in real PostgreSQL integration: RSMS_TEST_DATABASE_URL=<isolated test database>."""
import os
import secrets
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('RSMS_TEST_DATABASE_URL'), 'Set RSMS_TEST_DATABASE_URL to run PostgreSQL integration')
class PostgresIntegrationTest(unittest.TestCase):
    def test_existing_business_flows_on_postgres(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        base = os.environ['RSMS_TEST_DATABASE_URL']
        suites = unittest.defaultTestLoader.loadTestsFromNames(['test_accounts', 'test_lifecycle', 'test_phone_auth'])
        result = unittest.TestResult()
        def cases(suite):
            for item in suite:
                yield from cases(item) if isinstance(item, unittest.TestSuite) else [item]
        with psycopg.connect(base, autocommit=True, connect_timeout=5) as db:
            for test in cases(suites):
                schema = 'rsms_test_' + secrets.token_hex(8)
                db.execute('CREATE SCHEMA ' + schema)
                try:
                    url = make_conninfo(base, options='-csearch_path=' + schema)
                    with patch.dict(os.environ, {'DATABASE_URL': url}):
                        test.run(result)
                finally:
                    db.execute('DROP SCHEMA ' + schema + ' CASCADE')
        self.assertEqual(result.testsRun, 15)
        self.assertFalse(result.errors + result.failures, '\n'.join(error for _, error in result.errors + result.failures))
