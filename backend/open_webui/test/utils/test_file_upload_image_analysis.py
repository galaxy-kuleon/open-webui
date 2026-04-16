import sys
from types import ModuleType, SimpleNamespace

from open_webui.routers.files import process_uploaded_file


def _build_request(image_analysis_enabled: bool):
    config = SimpleNamespace(
        STT_SUPPORTED_CONTENT_TYPES=[],
        IMAGE_ANALYSIS_ENABLED=image_analysis_enabled,
        CONTENT_EXTRACTION_ENGINE='',
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def test_process_uploaded_file_runs_image_analysis_when_enabled(monkeypatch):
    request = _build_request(image_analysis_enabled=True)
    file_obj = SimpleNamespace(content_type='image/png')
    file_item = SimpleNamespace(id='file-123')

    monkeypatch.setattr('open_webui.routers.files._is_text_file', lambda *_args, **_kwargs: False)

    processed = []

    def _unexpected_process_file(*args, **kwargs):
        raise AssertionError('process_file should not run for image analysis path')

    monkeypatch.setattr('open_webui.routers.files.process_file', _unexpected_process_file)

    fake_module = ModuleType('open_webui.utils.image_analysis')

    def _analyze_image(app, file_id, file_path, content_type):
        processed.append(
            {
                'app': app,
                'file_id': file_id,
                'file_path': file_path,
                'content_type': content_type,
            }
        )

    fake_module.analyze_image = _analyze_image
    monkeypatch.setitem(sys.modules, 'open_webui.utils.image_analysis', fake_module)

    process_uploaded_file(
        request=request,
        file=file_obj,
        file_path='/tmp/image.png',
        file_item=file_item,
        file_metadata={},
        user=SimpleNamespace(id='user-1'),
        db=object(),
    )

    assert processed == [
        {
            'app': request.app,
            'file_id': 'file-123',
            'file_path': '/tmp/image.png',
            'content_type': 'image/png',
        }
    ]
