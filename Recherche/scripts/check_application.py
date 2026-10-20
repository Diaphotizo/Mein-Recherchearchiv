"""Read-only HTTP checks; also works with a fresh, empty archive."""
import argparse
import http.client
import json
from urllib.parse import urlencode


def check(port):
    def request(path, headers=None, method="GET", body=None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def data(path):
        status, headers, body = request(path)
        assert status == 200, (path, status, body[:200])
        return json.loads(body)

    health = data("/api/health")
    assert health["portal"] is True
    enabled = {c["id"] for c in data("/api/collections")["collections"] if c["enabled"]}
    assert enabled == {"bip", "courts", "un"}
    counts = {}
    for collection, prefix in (("bip", "/api"), ("courts", "/api/courts"), ("un", "/api/un")):
        state = data(prefix + "/state")
        results = data(prefix + "/search")
        assert state["total"] >= 0 and results["total"] >= 0
        assert isinstance(results["items"], list)
        counts[collection] = state["total"]
        if collection != "bip" and results["items"]:
            item = results["items"][0]
            pdf = prefix + "/pdf?" + urlencode({"id": item["id"]})
            status, headers, body = request(pdf, {"Range": "bytes=0-31"})
            assert status == 206 and len(body) == 32 and body.startswith(b"%PDF-")
            assert headers["Content-Range"].startswith("bytes 0-31/")
    for path in ("/", "/icc-icj/", "/un/", "/portal/collections.js", "/app.css",
                 "/portal/un.svg", "/portal/courts.svg"):
        assert request(path)[0] == 200, path
    assert request("/api/courts/state", {"Host": "untrusted.example"})[0] == 403
    for prefix in ("/api", "/api/courts", "/api/un"):
        assert request(prefix + "/update", method="POST", body="{}",
                       headers={"Content-Type": "application/json"})[0] == 403
    assert request("/api/courts/pdf?id=../../private")[0] in (400, 404)
    return {"passed": True, "documents": counts,
            "checks": ["three collections", "state/search APIs", "shared assets",
                       "Host and CSRF protection", "PDF path rejection"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    print(json.dumps(check(args.port), ensure_ascii=False, indent=2))
