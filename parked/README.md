# Parked work for the next update

Not collected by the test suite (it lives outside `tests/`). See [docs/ROADMAP.md](../docs/ROADMAP.md).

- `test_host_replace_after_update.py`: tests for replacing a running studio host after an update. The
  implementation that went with it was dropped on review (it stopped the host in-process and by pid);
  the agreed design is the authenticated `POST /api/host/close` with `X-Studio-Token`, refusing while a
  take is queued or running.
