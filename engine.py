"""Persistent inference subprocess: kill its process group on timeout/crash."""
import asyncio
import json
import os
import signal
import sys


class Engine:
    def __init__(self, settings, command=None):
        self.s = settings
        self.command = command or [sys.executable, '-u', 'inference_worker.py']
        self.process = None

    async def start(self):
        if self.process and self.process.returncode is None:
            return
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True, limit=4 * 1024 * 1024)
        try:
            response = await asyncio.wait_for(self.process.stdout.readline(), self.s.startup_seconds)
            if json.loads(response).get('ready') is not True:
                raise RuntimeError('Model startup failed')
        except BaseException:
            await self.close()
            raise

    async def run(self, source):
        try:
            await self.start()
            async def exchange():
                self.process.stdin.write((json.dumps({'source': str(source)}) + '\n').encode())
                await self.process.stdin.drain()
                return await self.process.stdout.readline()
            raw = await asyncio.wait_for(exchange(), self.s.job_seconds)
            response = json.loads(raw)
            if 'error' in response:
                # Recreate process after any failed inference (including OOM).
                await self.close()
                raise RuntimeError(response['error'])
            return response['result']
        except asyncio.TimeoutError:
            await self.close()
            raise RuntimeError('Inference timed out') from None
        except BaseException:
            await self.close()
            raise

    async def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        # Even if the parent died, ffmpeg descendants may still be in this group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
