import ssl
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import algocode_cli as cli


class TlsTests(unittest.TestCase):
    def test_verifies_certificates_and_hostname(self):
        context = cli.tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    @unittest.skipUnless(sys.platform == 'darwin' and Path('/etc/ssl/cert.pem').is_file(), 'Нужен системный PEM macOS')
    def test_loads_system_roots_even_without_python_ca_bundle(self):
        empty_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.assertEqual(empty_context.cert_store_stats()['x509_ca'], 0)
        with patch.object(cli.ssl, 'create_default_context', return_value=empty_context):
            context = cli.tls_context()
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_request_uses_configured_context(self):
        context = cli.tls_context()
        with patch.object(cli, 'tls_context', return_value=context), patch.object(cli, 'urlopen') as open_url:
            cli.open_url('https://algocode.ru/')
        self.assertIs(open_url.call_args.kwargs['context'], context)
        self.assertEqual(open_url.call_args.args[0].full_url, 'https://algocode.ru/')


if __name__ == '__main__':
    unittest.main()
