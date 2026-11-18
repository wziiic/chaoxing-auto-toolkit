"""Probe the configured model origin without question text or authentication."""
from urllib.parse import urlsplit, urlunsplit
import requests


class NoAuthentication(requests.auth.AuthBase):
    def __call__(self, request):
        return request  # Also prevents requests from reading a user's .netrc.


def model_connection_available(config):
    try:
        parts=urlsplit(config.get('llm',{}).get('base_url',''))
        if parts.scheme not in ('http','https') or not parts.hostname:
            return False
        host='['+parts.hostname+']' if ':' in parts.hostname else parts.hostname
        if parts.port:host+=':'+str(parts.port)
        address=urlunsplit((parts.scheme,host,parts.path or '/','',''))
        # Use exactly the same default proxy/TLS policy as call_llm. An HTTP
        # error still proves transport reachability; authentication is checked
        # only by the subsequent normal model request. Never follow redirects.
        response=requests.get(address,timeout=(5,8),allow_redirects=False,stream=True,auth=NoAuthentication())
        response.close()
        return True
    except (requests.RequestException,ValueError):
        return False
