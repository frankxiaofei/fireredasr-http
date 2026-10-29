"""No HTTP in this process. stdout is reserved for the JSON protocol."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import wave


def transcribe(model, source):
    import torch
    target = source.parent / 'audio.wav'
    subprocess.run(
        ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
         '-format_whitelist', 'wav,mp3,mov,matroska,webm,ogg,flac,aac', '-i', str(source), '-t', '61', '-vn', '-ar', '16000', '-ac', '1',
         '-c:a', 'pcm_s16le', str(target)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    with wave.open(str(target)) as audio:
        duration = audio.getnframes() / audio.getframerate()
    if not 0 < duration <= 60:
        raise ValueError('Audio must be between 0 and 60 seconds')
    with torch.inference_mode():
        result = model.transcribe(['request'], [str(target)])[0]
    return {'text': result['text'], 'duration': duration,
            'confidence': result.get('confidence'), 'timestamp': result.get('timestamp', [])}


def main():
    protocol = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from fireredasr2s.fireredasr2 import FireRedAsr2, FireRedAsr2Config
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable')
        model = FireRedAsr2.from_pretrained('aed', os.environ.get('MODEL_DIR', 'models/FireRedASR2-AED'),
                                          FireRedAsr2Config(use_gpu=True, use_half=False, return_timestamp=True))
    print(json.dumps({'ready': True}), file=protocol, flush=True)
    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = transcribe(model, Path(json.loads(line)['source']))
            response = {'result': result}
        except ValueError:
            response = {'error': 'Invalid audio: maximum duration is 60 seconds'}
        except Exception:
            # Do not expose transcript, local paths or model exception text to clients.
            response = {'error': 'Audio decoding or inference failed'}
        print(json.dumps(response, ensure_ascii=False), file=protocol, flush=True)


if __name__ == '__main__':
    main()
