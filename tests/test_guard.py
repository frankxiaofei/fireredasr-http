import asyncio
import pytest
from guard import RequestGuard
from config import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected', [('huge_header',413),
    ('chunked',413), ('slow',408), ('disconnect',400), ('slots',429)])
async def test_preparse_guard(tmp_path, case, expected):
    called = False
    received = 0
    messages = []
    s = Settings(data_dir=tmp_path, min_free=0, max_upload=10, upload_seconds=0.02)
    async def downstream(scope, receive, send):
        nonlocal called
        called = True
    guard = RequestGuard(downstream, s)
    headers = []
    if case == 'huge_header': headers.append((b'content-length', b'999999'))
    if case == 'slots': guard.uploads = 2
    async def receive():
        nonlocal received
        received += 1
        if case == 'slow': await asyncio.sleep(1)
        if case == 'disconnect': return {'type':'http.disconnect'}
        return {'type':'http.request', 'body':b'x'*70000, 'more_body':True}
    async def send(message): messages.append(message)
    await guard({'type':'http','path':'/v1/transcription-jobs','method':'POST','headers':headers}, receive, send)
    assert messages[0]['status'] == expected
    assert not called
    if case in ('huge_header','slots'): assert received == 0
    if case != 'slots': assert guard.uploads == 0
