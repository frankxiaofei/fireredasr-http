import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    api_key: str
    data_dir: Path = Path('data')
    capacity: int = 16
    max_upload: int = 20 * 1024 * 1024
    upload_seconds: float = 60
    upload_slots: int = 2
    min_free: int = 1024 * 1024 * 1024
    job_seconds: float = 180
    startup_seconds: float = 300
    result_ttl: int = 3600
    max_results: int = 1000
    rate_per_minute: int = 120

    def __post_init__(self):
        if len(self.api_key) < 24 or not self.api_key.isascii():
            raise ValueError('API_KEY must contain at least 24 ASCII characters')
        for name in ('capacity', 'max_upload', 'upload_seconds', 'upload_slots',
                     'job_seconds', 'startup_seconds', 'result_ttl', 'max_results', 'rate_per_minute'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if self.min_free < 0:
            raise ValueError('min_free must be nonnegative')
        self.data_dir = Path(self.data_dir).resolve()

    @classmethod
    def from_env(cls):
        return cls(api_key=os.environ.get('API_KEY', ''),
                   data_dir=Path(os.environ.get('DATA_DIR', 'data')),
                   capacity=int(os.environ.get('MAX_ACTIVE_JOBS', '16')),
                   min_free=int(os.environ.get('MIN_FREE_BYTES', str(1024**3))),
                   job_seconds=float(os.environ.get('JOB_TIMEOUT_SECONDS', '180')),
                   result_ttl=int(os.environ.get('RESULT_TTL_SECONDS', '3600')))
