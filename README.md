# build-mon

Monitoring dashboard for the headless build host agents-home (see [intent.md](intent.md)).

```sh
docker compose up -d --build
```

Open http://localhost:8080 and enter the passphrase for `~/.ssh/id_ed25519_agents-home`.
The key stays unlocked in the container's ssh-agent until the container restarts.

- `server.py` serves the page and runs `collect.py` on agents-home over SSH (`BM_HOST` in `compose.yaml`).
- Updates started from the dashboard run in the tmux session `build-mon` on agents-home; the Screen tool shows any tmux session.
- Check the collector on its own: `ssh agents@192.168.0.117 python3 - selftest < collect.py`
