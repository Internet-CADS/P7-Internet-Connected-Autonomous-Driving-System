"""Issues 09 and 10: result files say which image (or plain processes) produced them."""

from icads.protocol.runtime import image_identity, service_cpu_limit


def test_image_identity(monkeypatch, tmp_path):
    monkeypatch.delenv("ICADS_IMAGE_ID", raising=False)
    assert image_identity(build_file=tmp_path / "missing") == "none (plain processes)"
    f = tmp_path / "IMAGE_BUILD_ID"
    f.write_text("3f2a\n")
    assert image_identity(build_file=f) == "build 3f2a"
    monkeypatch.setenv("ICADS_IMAGE_ID", "sha256:abc")
    assert image_identity(build_file=f) == "sha256:abc"


def test_service_cpu_limit(monkeypatch):
    monkeypatch.delenv("ICADS_SERVICE_CPUS", raising=False)
    assert service_cpu_limit() == "none (plain processes)"
    monkeypatch.setenv("ICADS_SERVICE_CPUS", "1")
    assert service_cpu_limit() == "1 CPU per replica"
