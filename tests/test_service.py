import asyncio
import time
from pathlib import Path

import httpx
import pytest
from config import Settings
from jobs import Store
from server import create_app


class FakeEngine:
    def __init__(self, delay=0.01):
        self.delay = delay
        self.calls = []
        self.concurrent = 0
        self.peak = 0

    async def start(self):
        pass

    async def close(self):
        pass

    async def run(self, source):
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        try:
            await asyncio.sleep(self.delay)
            text = source.read_text()
            self.calls.append(text)
            if text == 'bad':
                raise RuntimeError('Mock decoding failed')
            return {'text': text, 'duration': 1}
        finally:
            self.concurrent -= 1


def settings(tmp_path, **kw):
    return Settings(data_dir=tmp_path, min_free=0, **kw)


async def finish(client, job_id):
    for _ in range(300):
        response = await client.get('/v1/transcription-jobs/' + job_id)
        job = response.json()
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError('Job did not finish')


@pytest.mark.asyncio
async def test_fifo_capacity_failure_cancel_without_auth(tmp_path):
    engine = FakeEngine(0.1)
    app = create_app(settings(tmp_path, capacity=3), engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            ids = []
            for text in ('first', 'bad', 'cancel'):
                r = await c.post('/v1/transcription-jobs', files={'file': ('a', text)})
                assert r.status_code == 202
                ids.append(r.json()['id'])
            assert (await c.post('/v1/transcription-jobs',
                                 files={'file': ('a', 'overflow')})).status_code == 429
            r = await c.delete('/v1/transcription-jobs/' + ids[2])
            assert r.json()['status'] == 'cancelled'
            assert (await finish(c, ids[0]))['result']['text'] == 'first'
            assert (await finish(c, ids[1]))['status'] == 'failed'
            assert engine.calls == ['first', 'bad']
            assert engine.peak == 1
            assert not any(p.is_dir() for p in tmp_path.iterdir())
            assert (await c.get('/v1/transcription-jobs/' + ids[0])).status_code == 200
            assert (await c.get('/v1/transcription-jobs/missing')).status_code == 404


@pytest.mark.asyncio
async def test_empty_oversize_disk_and_sync(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path, max_upload=10), FakeEngine())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            for content, code in [(b'', 400), (b'x' * 11, 413)]:
                r = await c.post('/v1/transcription-jobs', files={'file': ('a', content)})
                assert r.status_code == code
                assert app.state.jobs.store.active == 0
            r = await c.post('/v1/audio/transcriptions', files={'file': ('a', b'hello')})
            assert r.status_code == 200 and r.json()['text'] == 'hello'
            from collections import namedtuple
            usage = namedtuple('usage', 'total used free')(100, 100, 0)
            monkeypatch.setattr('shutil.disk_usage', lambda _: usage)
            assert (await c.post('/v1/transcription-jobs',
                                 files={'file': ('a', b'x')})).status_code == 507


def test_recovery_expiry_lock(tmp_path):
    s = settings(tmp_path)
    store = Store(s)
    with pytest.raises(RuntimeError, match='already in use'):
        Store(s)
    queued = store.reserve()
    store.folder(queued).mkdir()
    (store.folder(queued) / 'input').write_text('saved')
    store.change(queued, status='queued')
    interrupted = store.reserve()
    store.change(interrupted, status='running')
    store.close()
    reopened = Store(s)
    assert reopened.items[queued]['status'] == 'queued'
    assert reopened.items[interrupted]['status'] == 'failed'
    reopened.change(interrupted, finished_at=time.time() - 3601)
    reopened.prune()
    assert interrupted not in reopened.items
    reopened.close()


@pytest.mark.asyncio
async def test_queued_jobs_resume(tmp_path):
    s = settings(tmp_path)
    store = Store(s)
    job = store.reserve()
    store.folder(job).mkdir()
    (store.folder(job) / 'input').write_text('restored')
    store.change(job, status='queued')
    store.close()
    engine = FakeEngine()
    app = create_app(s, engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            assert (await finish(c, job))['result']['text'] == 'restored'


@pytest.mark.asyncio
async def test_rate_limit(tmp_path):
    app = create_app(settings(tmp_path, rate_per_minute=1), FakeEngine())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            assert (await c.get('/v1/status')).status_code == 200
            assert (await c.get('/v1/status')).status_code == 429


def test_settings_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv('API_KEY', raising=False)
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    assert Settings.from_env().data_dir == tmp_path.resolve()


def test_legacy_api_key_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv('API_KEY', 'old-unused-value')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    assert not hasattr(Settings.from_env(), 'api_key')


@pytest.mark.asyncio
async def test_twenty_simultaneous_requests_bounded(tmp_path):
    engine = FakeEngine(0.2)
    app = create_app(settings(tmp_path, capacity=4), engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            async def post(i):
                return await c.post('/v1/transcription-jobs',
                                    files={'file': ('audio', str(i))})
            responses = await asyncio.gather(*(post(i) for i in range(20)))
            accepted = [r.json()['id'] for r in responses if r.status_code == 202]
            assert 1 <= len(accepted) <= 4
            assert all(r.status_code in (202, 429) for r in responses)
            assert app.state.jobs.store.active <= 4
            for job_id in accepted:
                assert (await finish(c, job_id))['status'] == 'succeeded'
            assert engine.peak == 1


@pytest.mark.asyncio
async def test_disconnect_waiter_does_not_cancel_durable_task(tmp_path):
    engine = FakeEngine(0.15)
    app = create_app(settings(tmp_path), engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            request = asyncio.create_task(c.post('/v1/audio/transcriptions',
                                               files={'file': ('a', 'retained')}))
            for _ in range(100):
                if app.state.jobs.store.items:
                    break
                await asyncio.sleep(0.005)
            job_id = next(iter(app.state.jobs.store.items))
            request.cancel()
            with pytest.raises(asyncio.CancelledError): await request
            assert (await finish(c, job_id))['result']['text'] == 'retained'
