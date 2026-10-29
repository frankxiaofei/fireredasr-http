import contextlib
import shutil
import subprocess
import sys
import types
import wave

import pytest
from inference_worker import transcribe

pytestmark = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg unavailable')


class Model:
    def transcribe(self, ids, paths):
        return [{'text': 'mock', 'confidence': 0.9, 'timestamp': []}]


@pytest.mark.parametrize('seconds,valid', [(1, True), (60, True), (61, False)])
def test_real_ffmpeg_duration_boundary(tmp_path, monkeypatch, seconds, valid):
    monkeypatch.setitem(sys.modules, 'torch', types.SimpleNamespace(inference_mode=contextlib.nullcontext))
    source = tmp_path / 'input'
    with wave.open(str(source), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * (16000 * seconds))
    if valid:
        assert transcribe(Model(), source)['duration'] == seconds
    else:
        with pytest.raises(ValueError): transcribe(Model(), source)


def test_corrupt_audio(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'torch', types.SimpleNamespace(inference_mode=contextlib.nullcontext))
    source = tmp_path / 'input'
    source.write_text('not audio')
    with pytest.raises(subprocess.CalledProcessError): transcribe(Model(), source)
