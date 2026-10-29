"""Authentication and bounded body buffering BEFORE multipart parsing."""
import asyncio
import secrets
import shutil
import tempfile
import time
from starlette.responses import JSONResponse


class RequestGuard:
    def __init__(self, app, settings):
        self.app = app
        self.s = settings
        self.uploads = 0
        self.tokens = float(settings.rate_per_minute)
        self.last = time.monotonic()

    async def reject(self, scope, receive, send, code, message):
        headers = {'Retry-After': '5'} if code in (429, 503) else None
        await JSONResponse({'detail': message}, status_code=code, headers=headers)(scope, receive, send)

    def allow_rate(self):
        now = time.monotonic()
        self.tokens = min(self.s.rate_per_minute,
                          self.tokens + (now - self.last) * self.s.rate_per_minute / 60)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] == '/health':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        expected = ('Bearer ' + self.s.api_key).encode('ascii')
        if not secrets.compare_digest(headers.get(b'authorization', b''), expected):
            return await self.reject(scope, receive, send, 401, 'Invalid API key')
        if not self.allow_rate():
            return await self.reject(scope, receive, send, 429, 'Rate limit exceeded')
        if scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        service = scope.get('app')
        jobs = getattr(getattr(service, 'state', None), 'jobs', None)
        if jobs and (jobs.task.done() or jobs.cleaner.done() or jobs.stopping):
            return await self.reject(scope, receive, send, 503, 'Worker unavailable')
        if jobs and jobs.store.active >= self.s.capacity:
            return await self.reject(scope, receive, send, 429, 'Queue full')
        if self.uploads >= self.s.upload_slots:
            return await self.reject(scope, receive, send, 429, 'Too many concurrent uploads')
        # Include a bounded allowance for multipart framing.
        limit = self.s.max_upload + 64 * 1024
        try:
            length = int(headers.get(b'content-length', b'0'))
            if length < 0:
                raise ValueError()
        except ValueError:
            return await self.reject(scope, receive, send, 400, 'Invalid Content-Length')
        if length > limit:
            return await self.reject(scope, receive, send, 413, 'Request too large')
        if shutil.disk_usage(self.s.data_dir).free < self.s.min_free + limit * 2:
            return await self.reject(scope, receive, send, 507, 'Insufficient disk space')
        self.uploads += 1
        try:
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=self.s.data_dir) as body:
                code = await self.buffer(receive, body, limit)
                if code:
                    return await self.reject(scope, receive, send, code, 'Upload rejected or timed out')
                body.seek(0)
                finished = False
                async def replay():
                    nonlocal finished
                    if finished:
                        return await receive()
                    chunk = body.read(64 * 1024)
                    finished = len(chunk) < 64 * 1024
                    return {'type': 'http.request', 'body': chunk, 'more_body': not finished}
                await self.app(scope, replay, send)
        finally:
            self.uploads -= 1

    async def buffer(self, receive, body, limit):
        deadline = time.monotonic() + self.s.upload_seconds
        total = 0
        while True:
            try:
                event = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return 408
            if event['type'] == 'http.disconnect':
                return 400
            chunk = event.get('body', b'')
            total += len(chunk)
            if total > limit:
                return 413
            body.write(chunk)
            if not event.get('more_body', False):
                return 0
