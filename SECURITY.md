# Security and deployment boundary

This project is intended for trusted internal callers sharing one API key. It is not a multi-tenant public transcription platform.

Use a dedicated unprivileged account, TLS termination, ingress connection/rate limits and a maintained ffmpeg package. Do not mount sensitive host directories into an inference container. Keep GPU drivers and dependencies patched. No guarantee is made that audio decoders are safe against all crafted inputs.

Never submit API keys, model credentials, private recordings or transcripts in issues. Report reproducible defects using synthetic inputs with secrets removed. For suspected vulnerabilities, use GitHub private vulnerability reporting if enabled; otherwise contact the repository owner privately.

The service does not guarantee exactly-once execution, encrypted storage or automatic recovery of an in-progress GPU inference. Queued jobs survive restart; interrupted jobs become failed. See README for retention and resource limits.
