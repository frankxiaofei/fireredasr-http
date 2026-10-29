import asyncio
import sys
import pytest
from config import Settings
from engine import Engine


@pytest.mark.asyncio
async def test_hard_timeout_kills_and_can_restart(tmp_path):
    script = tmp_path / 'fake.py'
    script.write_text('import json,sys,time\nprint(json.dumps({"ready":True}),flush=True)\nfor line in sys.stdin:\n time.sleep(10)\n')
    s = Settings(api_key='a'*32, data_dir=tmp_path, job_seconds=0.05, startup_seconds=3)
    engine = Engine(s, [sys.executable, '-u', str(script)])
    await engine.start()
    process = engine.process
    with pytest.raises(RuntimeError, match='timed out'):
        await engine.run(tmp_path / 'input')
    assert process.returncode is not None
    assert engine.process is None
    await engine.start()
    assert engine.process.pid != process.pid
    await engine.close()


@pytest.mark.asyncio
async def test_child_crash_and_startup_failure(tmp_path):
    s = Settings(api_key='a'*32, data_dir=tmp_path, startup_seconds=3)
    engine = Engine(s, [sys.executable, '-c', 'print("bad",flush=True)'])
    with pytest.raises(Exception): await engine.start()
    assert engine.process is None
    code = 'import json; print(json.dumps({"ready":True}),flush=True)'
    engine = Engine(s, [sys.executable, '-c', code])
    await engine.start()
    with pytest.raises(Exception): await engine.run(tmp_path / 'input')
    assert engine.process is None
