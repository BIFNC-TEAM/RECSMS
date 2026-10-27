"""Run with RSMS_TEST_REDIS_URL against a disposable Redis instance."""
import os
import secrets
import time
import unittest
from unittest.mock import patch
import shared_cache


@unittest.skipUnless(os.environ.get('RSMS_TEST_REDIS_URL'), 'Set RSMS_TEST_REDIS_URL for Redis integration')
class RedisIntegrationTest(unittest.TestCase):
    def test_session_ttl_revocation_and_atomic_rate_limit(self):
        with patch.dict(os.environ, {'REDIS_URL':os.environ['RSMS_TEST_REDIS_URL'], 'REDIS_PREFIX':'rsms-check:'+secrets.token_hex(8)+':'}):
            shared_cache.client.cache_clear()
            token=secrets.token_urlsafe(32)
            keys=[shared_cache.key('session',token),shared_cache.key('rate','login-check')]
            try:
                shared_cache.create(None,token,42,time.time()+120)
                self.assertEqual(shared_cache.user_id(None,token),42)
                self.assertGreater(shared_cache.client().ttl(keys[0]),0)
                self.assertNotIn(token,keys[0])
                shared_cache.remove(None,token)
                self.assertIsNone(shared_cache.user_id(None,token))
                for _ in range(15):
                    self.assertFalse(shared_cache.limited({},'login-check',15))
                self.assertTrue(shared_cache.limited({},'login-check',15))
                with patch.object(shared_cache.client(),'get',side_effect=ConnectionError('fixture Redis down')):
                    with self.assertRaises(ConnectionError):
                        shared_cache.user_id(None,token)
            finally:
                shared_cache.client().delete(*keys)
                shared_cache.client.cache_clear()
