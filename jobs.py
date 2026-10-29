import asyncio
import fcntl
import json
import logging
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

ACTIVE = ('uploading', 'queued', 'running')
TERMINAL = ('succeeded', 'failed', 'cancelled')


class Full(Exception):
    pass


class Store:
    def __init__(self, settings):
        self.s = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (settings.data_dir / 'service.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Data directory already in use; run exactly one worker') from None
        self.db = sqlite3.connect(settings.data_dir / 'jobs.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()
        self.items = {row[0]: json.loads(row[1]) for row in self.db.execute('SELECT id, body FROM jobs')}
        self.recover()

    def save(self, job):
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job['id'], json.dumps(job)))
        self.db.commit()
        self.items[job['id']] = job

    def folder(self, job_id):
        return self.s.data_dir / job_id

    def recover(self):
        for job in list(self.items.values()):
            if job['status'] in ('uploading', 'running'):
                self.finish(job['id'], 'failed', error='Service interrupted; submit again')
            elif job['status'] in TERMINAL:
                shutil.rmtree(self.folder(job['id']), ignore_errors=True)
            elif not (self.folder(job['id']) / 'input').is_file():
                self.finish(job['id'], 'failed', error='Saved audio missing')
        # Only delete UUID directories owned by this service.
        for folder in self.s.data_dir.iterdir():
            if folder.is_dir() and len(folder.name) == 32 and all(c in '0123456789abcdef' for c in folder.name):
                if folder.name not in self.items:
                    shutil.rmtree(folder)
        self.prune()

    @property
    def active(self):
        return sum(j['status'] in ACTIVE for j in self.items.values())

    def reserve(self):
        self.prune()
        if self.active >= self.s.capacity:
            raise Full()
        job = {'id': uuid.uuid4().hex, 'status': 'uploading', 'created_at': time.time()}
        self.save(job)
        return job['id']

    def change(self, job_id, **fields):
        job = dict(self.items[job_id], **fields)
        self.save(job)

    def finish(self, job_id, status, **fields):
        self.change(job_id, status=status, finished_at=time.time(), **fields)
        shutil.rmtree(self.folder(job_id), ignore_errors=True)

    def prune(self):
        done = sorted((j for j in self.items.values() if j['status'] in TERMINAL),
                      key=lambda j: j['finished_at'])
        excess = max(0, len(done) - self.s.max_results)
        for i, job in enumerate(done):
            if i < excess or time.time() - job['finished_at'] > self.s.result_ttl:
                self.db.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
                self.items.pop(job['id'], None)
        self.db.commit()

    def close(self):
        self.db.close()
        self.lock.close()


class Jobs:
    def __init__(self, settings, engine):
        self.store = Store(settings)
        self.engine = engine
        self.queue = asyncio.Queue()
        self.task = None
        self.cleaner = None
        self.stopping = False

    def start(self):
        for job in sorted(self.store.items.values(), key=lambda j: j.get('queued_at', j['created_at'])):
            if job['status'] == 'queued':
                self.queue.put_nowait(job['id'])
        self.task = asyncio.create_task(self.run())
        self.cleaner = asyncio.create_task(self.cleanup())

    def enqueue(self, job_id):
        self.store.change(job_id, status='queued', queued_at=time.time())
        self.queue.put_nowait(job_id)

    async def cleanup(self):
        while True:
            await asyncio.sleep(30)
            self.store.prune()

    async def run(self):
        while not self.stopping:
            job_id = await self.queue.get()
            if job_id is None:
                return
            job = self.store.items.get(job_id)
            if not job or job['status'] != 'queued':
                continue
            self.store.change(job_id, status='running', started_at=time.time())
            try:
                result = await self.engine.run(self.store.folder(job_id) / 'input')
                self.store.finish(job_id, 'succeeded', result=result)
            except asyncio.CancelledError:
                self.store.finish(job_id, 'failed', error='Service stopped during inference')
                raise
            except Exception as exc:
                logging.getLogger('asr').warning('Job %s failed (%s)', job_id, type(exc).__name__)
                # Only engine-authored messages are safe; no arbitrary exception strings.
                message = str(exc) if type(exc) is RuntimeError else 'Inference failed'
                self.store.finish(job_id, 'failed', error=message)

    async def close(self):
        self.stopping = True
        self.cleaner.cancel()
        self.task.cancel()
        await asyncio.gather(self.cleaner, self.task, return_exceptions=True)
        await self.engine.close()
        self.store.close()
