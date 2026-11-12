# Step 4: agent-guard config

## Files
- `canary_leak_bench/recorder.py`, `guard.py`
- `tests/test_recorder.py`, `tests/test_guard.py`

## Recorder
Threading HTTP server on `127.0.0.1:0`. Forwards `POST /v1/messages` to a
configured upstream with a deadline and a 1 MiB cap. Copies `content-type`,
`x-api-key`, and `anthropic-version` and never logs them. Scans only the
response's assistant content. Request bodies contain the canary by design and
must not count. Flags live under one lock. The runner reads them after the
client call returns.

## Guard subprocess
Starts `agent-guard -listen 127.0.0.1:0 -audit-preview=false` with a generated
policy (tools fetch_page and post_reply, hosts empty, sensitive classes block).
Stderr is read on one thread so the pipe cannot fill. Ready when a line
matches `addr=127.0.0.1:<port>`, with a 5s deadline. `stop()` sends SIGTERM and
joins with a deadline, then kills. The runner thread owns the process handle.

Live `agent-guard` config: harness talks to the proxy, proxy's upstream is the
recorder, recorder's upstream is the provider base URL. `tool_hit` is the
recorder's flag. Missing binary: `ConfigError` before `LiveAgent` is constructed
and before any reserve.

## Tests
- Recorder against a local upstream: response tool input with the canary sets
  `tool_hit`; a request that contains the canary and a clean response does not.
  The recorder's error text does not contain the key.
- `require_binary` raises when the path is missing. No test requires the Go
  binary; a subprocess test runs only when `AGENT_GUARD_BIN` is set and skips
  otherwise, with a 5s ready deadline.
