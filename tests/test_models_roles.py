from kairo.models import Constitution


def test_image_exts_map_to_attachment():
    rbe = Constitution().roles_by_ext
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".heic"):
        assert rbe[ext] == "attachment", ext


def test_audio_and_document_roles_unchanged():
    rbe = Constitution().roles_by_ext
    assert rbe[".m4a"] == "audio"
    assert rbe[".pdf"] == "document"
