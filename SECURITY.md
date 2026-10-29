# Security and deployment boundary

This service intentionally has no authentication. All API routes are callable without credentials; access is determined by network reachability. It does not provide tenant isolation.

Use a dedicated unprivileged account, TLS termination, ingress connection/rate limits and a maintained ffmpeg package. Do not mount sensitive host directories into an inference container. Keep GPU drivers and dependencies patched. No guarantee is made that audio decoders are safe against all crafted inputs.

Never submit model credentials, private recordings or transcripts in issues. Report reproducible defects using synthetic inputs with secrets removed. For suspected vulnerabilities, use GitHub private vulnerability reporting if enabled; otherwise contact the repository owner privately.

The service does not guarantee exactly-once execution, encrypted storage or automatic recovery of an in-progress GPU inference. Queued jobs survive restart; interrupted jobs become failed. See README for retention and resource limits.
