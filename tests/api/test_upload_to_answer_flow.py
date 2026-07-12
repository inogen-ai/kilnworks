from kilnworks.worker.loop import run_worker

from tests.api.test_upload_endpoints import _headers, _upload


def test_upload_worker_ask_roundtrip(client, api_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(api_settings, "data_dir", str(tmp_path))
    headers = _headers(client, api_settings, email="flow@example.com")
    job_id = _upload(
        client, headers,
        filename="firing-guide.md",
        content=b"# Firing\n\nBisque firing happens at cone 06.",
    ).json()["job_id"]

    assert run_worker(api_settings, once=True) == 1
    assert client.get(f"/jobs/{job_id}", headers=headers).json()["status"] == "done"

    answer = client.post(
        "/ask", json={"question": "Bisque firing happens at cone 06."}, headers=headers
    ).json()
    assert answer["citations"]
    assert answer["citations"][0]["title"] == "firing-guide"
