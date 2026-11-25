# Deploying Dailies on a Linux server (systemd)

A hardened layout for an always-on box. The server listens on loopback only; a Cloudflare Tunnel publishes it, and
Cloudflare Access plus the server's own JWT check guard it (see the main README's security model).

```
/srv/dailies/                      root or deploy user, group dailies, 0750
  app/releases/<git-sha>/          a checkout with web/dist built (npm ci && npm run build in web/)
  app/current -> releases/<sha>    switch releases by moving this symlink, then restart
  app/venv/                        python -m venv; pip install -r server/requirements.txt
  projects/<name>/                 owned by dailies (the only writable path)
/etc/dailies/dailies.env           root:dailies 0640, from dailies.env.example
/etc/cloudflared/dailies.yml       from cloudflared.example.yml, plus the tunnel credential (root 0600)
```

```sh
sudo useradd --system --home /srv/dailies --shell /usr/sbin/nologin dailies
sudo chgrp dailies /srv/dailies /srv/dailies/projects && sudo chmod 0750 /srv/dailies /srv/dailies/projects
sudo chown -R dailies:dailies /srv/dailies/projects/<name>
sudo cp systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now dailies dailies-tunnel
```

Check it:

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: dailies.example.com' http://127.0.0.1:8765/api/versions  # 403: no token
curl -s -o /dev/null -w '%{http_code}\n' https://dailies.example.com/                                        # 302 to Access login
```

Traps:

- Every directory above the project must be traversable by the `dailies` group, or the unit fails with
  `status=200/CHDIR`.
- Run one connector per tunnel. A second host still running the same tunnel splits requests between the two.
- `/api/cut-proxy/...` answers `503 {"status":"generating"}` while a 720p proxy is first built; the player falls back
  to the full file meanwhile.
