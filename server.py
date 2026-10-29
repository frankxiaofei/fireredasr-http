import asyncio
import os
import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from config import Settings
from engine import Engine
from guard import RequestGuard
from jobs import Full, Jobs, TERMINAL


def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    engine = engine or Engine(settings)

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o077)
        jobs = Jobs(settings, engine)
        app.state.jobs = jobs
        try:
            await engine.start()
            jobs.start()
            yield
        finally:
            if jobs.task:
                await jobs.close()
            else:
                await engine.close()
                jobs.store.close()

    app = FastAPI(title='FireRedASR2 HTTP Queue', version='0.3.1', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestGuard, settings=settings)

    @app.get('/health')
    async def health():
        # Deliberately public, with no queue size / configuration disclosure.
        return {'status': 'ok'}

    @app.get('/v1/status')
    async def status():
        jobs = app.state.jobs
        ready = not jobs.task.done() and not jobs.cleaner.done()
        return JSONResponse({'ready': ready, 'active_jobs': jobs.store.active,
                             'capacity': settings.capacity}, status_code=200 if ready else 503)

    async def submit(file):
        jobs = app.state.jobs
        if jobs.task.done() or jobs.cleaner.done() or jobs.stopping:
            await file.close()
            raise HTTPException(503, 'Worker unavailable')
        if shutil.disk_usage(settings.data_dir).free < settings.min_free + settings.max_upload:
            await file.close()
            raise HTTPException(507, 'Insufficient disk space')
        try:
            job_id = jobs.store.reserve()
        except Full:
            await file.close()
            raise HTTPException(429, 'Queue full', headers={'Retry-After': '5'})
        try:
            folder = jobs.store.folder(job_id)
            folder.mkdir(mode=0o700)
            size = 0
            with (folder / 'input').open('wb') as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload:
                        raise HTTPException(413, 'Audio exceeds upload limit')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise HTTPException(400, 'Empty audio')
            jobs.enqueue(job_id)
            return job_id
        except BaseException:
            jobs.store.finish(job_id, 'failed', error='Upload failed')
            raise
        finally:
            await file.close()

    @app.post('/v1/transcription-jobs', status_code=202)
    async def create_job(file: UploadFile = File(...)):
        job_id = await submit(file)
        url = '/v1/transcription-jobs/' + job_id
        return JSONResponse({'id': job_id, 'status': 'queued', 'status_url': url},
                            status_code=202, headers={'Location': url})

    @app.get('/v1/transcription-jobs/{job_id}')
    async def get_job(job_id: str):
        app.state.jobs.store.prune()
        job = app.state.jobs.store.items.get(job_id)
        if job is None:
            raise HTTPException(404, 'Job not found or expired')
        return job

    @app.delete('/v1/transcription-jobs/{job_id}')
    async def cancel_job(job_id: str):
        store = app.state.jobs.store
        job = store.items.get(job_id)
        if job is None:
            raise HTTPException(404, 'Job not found or expired')
        if job['status'] == 'running':
            raise HTTPException(409, 'Running jobs cannot be cancelled')
        if job['status'] == 'queued':
            store.finish(job_id, 'cancelled')
        return store.items[job_id]

    @app.post('/v1/audio/transcriptions')
    async def synchronous(file: UploadFile = File(...)):
        job_id = await submit(file)
        # Bound HTTP waiting independently of the durable job lifetime.
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            job = app.state.jobs.store.items[job_id]
            if job['status'] == 'succeeded':
                return job['result']
            if job['status'] in TERMINAL:
                raise HTTPException(422, job.get('error', 'Job cancelled'))
            await asyncio.sleep(0.1)
        url = '/v1/transcription-jobs/' + job_id
        return JSONResponse({'id': job_id, 'status_url': url}, status_code=202,
                            headers={'Location': url})

    return app
