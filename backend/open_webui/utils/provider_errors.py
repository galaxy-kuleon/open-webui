import json
import logging

from open_webui.utils.misc import stream_chunks_handler

log = logging.getLogger(__name__)


def public_provider_error(status):
    message = 'The service could not complete this request. Please try again.'
    if status == 429:
        message = 'The service is temporarily busy. Please try again later.'
    elif status in (401, 403):
        message = 'The service is temporarily unavailable. Please try again later.'
    elif status == 413:
        message = 'The request is too large. Please reduce its size and try again.'
    return {'error': {'message': message, 'code': status}}


def public_provider_stream(stream):
    async def chunks():
        buffer = b''
        async for chunk in stream_chunks_handler(stream):
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                yield public_provider_line(line) + b'\n'
        if buffer:
            yield public_provider_line(buffer) + b'\n'
    return chunks()


def public_provider_line(line):
    if not line.startswith(b'data:'):
        return line
    try:
        payload = json.loads(line[5:])
    except (ValueError, UnicodeDecodeError):
        return line
    if not isinstance(payload, dict) or not payload.get('error'):
        return line
    log.error('provider_stream_error raw=%s', payload)
    error = payload['error']
    status = error.get('code', error.get('status', 502)) if isinstance(error, dict) else 502
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 502
    if status < 400 or status > 599:
        status = 502
    return b'data: ' + json.dumps(public_provider_error(status)).encode()
