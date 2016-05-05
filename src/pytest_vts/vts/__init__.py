import json
import re
import logging
import os.path

import requests
import responses


_logger = logging.getLogger(__name__)


class Recorder(object):
    """Video Test System, inspired by VHS

    Able to record/playback cassettes. Each cassette is made of tracks (one
    HTTP request-response pair).

    Depends of py.test and responses (or a http mocking lib which support
    callbacks as stub reponse)

    While .responses already keeps a collection of `.calls`, they're not
    directly json serializable therefore a json version of the `.cassette` is
    being constructed from `.calls`

    TODO: when a new request not recorded for a test happens the behaviour
    defined by the mocking library happens (e.g. `responses` will raise a
    requests.exceptions.ConnectionError)
    TODO: record the cassette only when the test has passed"""
    def __init__(self, pytest_req, basedir=None, cassette_name=None):
        self.cassette = []
        self.has_recorded = False
        self._pytst_req = pytest_req
        self.basedir = basedir or self._pytst_req.fspath.dirpath()
        self.cassette_name = cassette_name or self._pytst_req.node.name
        self.responses = responses.RequestsMock(
            assert_all_requests_are_fired=False)

    def setup(self):
        self.responses.start()
        if not self.has_cassette():
            self.setup_recording()
        else:
            self.setup_playback()

    def setup_recording(self):
        _logger.info("recording ...")
        self.responses.reset()
        all_re = re.compile("http.*")
        self.responses.add_callback(
            responses.GET, all_re,
            self.record())

    def setup_playback(self):
        _logger.info("playing back ...")
        self._insert_cassette()
        self.responses.reset()  # reset recording matchers
        self.rewind_cassette()

    def teardown(self):
        self.responses.stop()
        self.responses.reset()
        if self.has_recorded:
            self._cass_file().write(
                json.dumps(self.cassette, encoding="utf8"),
                ensure=True)

    def _cass_dir(self):
        return self.basedir.join("cassettes")

    def _test_name(self):
        filename = self.cassette_name.replace(os.path.sep, "_")
        return ".".join((filename, "cassette"))

    def _cass_file(self):
        return self._cass_dir().join(self._test_name())

    def has_cassette(self):
        return self._cass_file().exists()

    def play(self, response):
        def _callback(http_req):
            return (response['status_code'],
                    response['headers'],
                    json.dumps(response['body']))
        return _callback

    def rewind_cassette(self):
        for track in self.cassette:
            req = track['request']
            self.responses.add_callback(
                req['method'], req['url'],
                match_querystring=True,
                callback=self.play(track['response']))

    def _insert_cassette(self):
        if not self.has_recorded:
            data = self._cass_file().read_text("utf8")
            self.cassette = json.loads(data)

    def record(self):
        def _callback(http_prep_req):
            track = {}
            track["request"] = {
                "method": http_prep_req.method,
                "url": http_prep_req.url,
                "path": http_prep_req.path_url,
                "headers": dict(http_prep_req.headers.items()),
                "body": http_prep_req.body,
            }
            sess = requests.Session()
            self.responses.stop()
            try:
                resp = sess.send(http_prep_req, timeout=2)
            except:
                raise
            else:
                content_encoding = resp.headers.get("Content-Encoding", "")
                if "gzip" in content_encoding:
                    # the body has already been decoded by the actual http
                    # call (above requests.Sessions.send). Keeping the gzip
                    # encoding in the response headers of will force the
                    # calling functions (during recording or replaying)
                    # (using requests) to try to decode it again.

                    # when urllib3 sees a Content-Encoding header will try
                    # to decode the response using the specified encoding,
                    # thus we need to remove it since it's been already
                    # decoded
                    del resp.headers["Content-Encoding"]
                try:
                    body = resp.json()
                    bodys = json.dumps(body)
                except ValueError:
                    body = bodys = resp.text
                track["response"] = {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers.items()),
                    "body": body,
                    # "history": resp.history,  # TODO: serialize request.Response objects into json
                }
                self.cassette.append(track)
                self.has_recorded = True
                return (resp.status_code,
                        resp.headers,
                        bodys)
            finally:
                self.responses.start()
        return _callback
