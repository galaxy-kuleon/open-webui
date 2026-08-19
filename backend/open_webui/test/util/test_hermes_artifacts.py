from open_webui.routers.hermes_artifacts import build_artifact_target


ARTIFACT_ID = "a" * 32
SIGNATURE = "b" * 64


def test_build_artifact_target_keeps_fixed_origin_and_quotes_filename():
    target = build_artifact_target(
        "http://hermes-gateway:8642/",
        ARTIFACT_ID,
        "Client memo 中文.docx",
        1780759204,
        SIGNATURE,
    )

    assert target == (
        "http://hermes-gateway:8642/v1/artifacts/"
        f"{ARTIFACT_ID}/Client%20memo%20%E4%B8%AD%E6%96%87.docx/"
        f"download/1780759204/{SIGNATURE}"
    )


def test_build_artifact_target_rejects_traversal_and_bad_capabilities():
    assert build_artifact_target(
        "http://hermes-gateway:8642", ARTIFACT_ID, "../secret", 1, SIGNATURE
    ) is None
    assert build_artifact_target(
        "http://hermes-gateway:8642", "not-an-id", "memo.docx", 1, SIGNATURE
    ) is None
    assert build_artifact_target(
        "http://hermes-gateway:8642", ARTIFACT_ID, "memo.docx", 1, "bad"
    ) is None
    assert build_artifact_target(
        "file:///etc", ARTIFACT_ID, "memo.docx", 1, SIGNATURE
    ) is None
