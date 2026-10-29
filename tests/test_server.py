import json
import threading
import unittest
from http.client import HTTPConnection

from server import Handler, ThreadingHTTPServer
from test_importer import package


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def test_static_files_and_path_boundary(self):
        status, headers, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("想起速度トレーナー", data.decode())
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)
        self.assertEqual(self.request("GET", "/", headers={"Host": "remote.example"})[0], 403)

    def test_import_endpoint(self):
        status, _, content = self.request("POST", "/api/import?name=sample.apkg", package())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["cards"][0]["fields"][1]["text"], "あんか")
        self.assertEqual(self.request("POST", "/api/import?name=bad.apkg", b"bad")[0], 400)

    def test_cross_origin_and_upload_limits(self):
        self.assertEqual(self.request("POST", "/api/import?name=sample.apkg", b"x",
                                      {"Origin": "http://remote.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/import?name=sample.apkg", b"",
                                      {"Content-Length": "100000000"})[0], 413)


if __name__ == "__main__":
    unittest.main()
