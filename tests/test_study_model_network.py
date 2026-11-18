import sys
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import requests

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'panel')]
from study_model_network import model_connection_available,NoAuthentication


class ModelTransportTests(unittest.TestCase):
    def test_probe_strips_secrets_and_does_not_follow_redirects_or_read_body(self):
        response=MagicMock(status_code=401)
        with patch('study_model_network.requests.get',return_value=response) as get:
            self.assertTrue(model_connection_available({'llm':{'base_url':'https://user:secret@example.test/v1?api_key=secret#fragment','api_key':'private'}}))
        self.assertEqual(get.call_args.args,('https://example.test/v1',))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertTrue(get.call_args.kwargs['stream'])
        self.assertNotIn('headers',get.call_args.kwargs)
        response.close.assert_called_once()
        prepared=requests.Request('GET','https://example.test',auth=NoAuthentication()).prepare()
        self.assertNotIn('Authorization',prepared.headers)

    def test_bad_configuration_and_network_failure_are_not_reachable(self):
        for address in ('','file:///tmp/secret','https://host:invalid'):
            self.assertFalse(model_connection_available({'llm':{'base_url':address}}))
        with patch('study_model_network.requests.get',side_effect=requests.exceptions.ProxyError('unavailable')):
            self.assertFalse(model_connection_available({'llm':{'base_url':'https://example.test'}}))


if __name__=='__main__':unittest.main()
