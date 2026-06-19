from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _read(name):
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_every_realtime_page_loads_managed_connection_runtime():
    for page in ("host.html", "solo.html", "duo_guest.html", "guest.html"):
        html = _read(page)
        assert '<script src="/vox-connection.js"></script>' in html, page
        assert '<script src="/vox-socket.js"></script>' in html, page
        assert "new WebSocket" not in html, page


def test_each_real_flow_constructs_one_managed_socket():
    host = _read("host.html")
    assert host.count("new VoxSocket") == 4  # Solo, Duo local, Duo host, Room host
    assert _read("solo.html").count("new VoxSocket") == 1
    assert _read("duo_guest.html").count("new VoxSocket") == 1
    assert _read("guest.html").count("new VoxSocket") == 1


def test_solo_resends_config_and_audio_meta_from_open_handler():
    for page in ("host.html", "solo.html"):
        html = _read(page)
        assert "audio_meta" in html, page
        assert "onopen" in html, page
        assert "sendSoloAudioMeta" in html or "_sendSoloAudioMeta" in html, page


def test_service_worker_does_not_cache_dynamic_or_own_update_request():
    sw = _read("sw.js")
    for route in ("/api/", "/ws", "/room/", "/sw.js"):
        assert route in sw
    assert "request.mode === 'navigate'" in sw
    assert "caches.delete" in sw
    assert "vox-static-" in sw
