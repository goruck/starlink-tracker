#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target_dir="$HOME/develop/starlink-tracker"
data_dir="$HOME/.local/share/starlink-tracker"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$target_dir/static" "$target_dir/tests" "$data_dir" "$unit_dir"
if [ "$source_dir" != "$target_dir" ]; then
  for name in tracker.py supplemental.py assessment.py reporting.py requirements.txt README.md install.sh; do install -m 644 "$source_dir/$name" "$target_dir/$name"; done
  if [ ! -f "$target_dir/.installed" ]; then
    install -m 644 "$source_dir/mission.json" "$target_dir/mission.json"
  fi
  install -m 644 "$source_dir"/static/* "$target_dir/static/"
  install -m 644 "$source_dir"/tests/*.py "$target_dir/tests/"
fi
if [ ! -x "$target_dir/.venv/bin/python" ]; then /usr/bin/python3.12 -m venv "$target_dir/.venv"; fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$target_dir/.venv/bin/python" -r "$target_dir/requirements.txt"
else
  "$target_dir/.venv/bin/python" -m ensurepip
  "$target_dir/.venv/bin/python" -m pip install -r "$target_dir/requirements.txt"
fi
(cd "$target_dir" && "$target_dir/.venv/bin/python" -m unittest discover -s tests -v)
cat > "$unit_dir/starlink-tracker.service" <<'UNIT'
[Unit]
Description=Local Starlink orbit dashboard
After=network.target
[Service]
Type=simple
WorkingDirectory=%h/develop/starlink-tracker
Environment=STARLINK_DATA_DIR=%h/.local/share/starlink-tracker
ExecStart=%h/develop/starlink-tracker/.venv/bin/python %h/develop/starlink-tracker/tracker.py serve
Restart=on-failure
RestartSec=15
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=default.target
UNIT
cat > "$unit_dir/starlink-collector.service" <<'UNIT'
[Unit]
Description=Collect Starlink orbital elements and catalog status
After=network-online.target
[Service]
Type=oneshot
WorkingDirectory=%h/develop/starlink-tracker
Environment=STARLINK_DATA_DIR=%h/.local/share/starlink-tracker
ExecStart=%h/develop/starlink-tracker/.venv/bin/python %h/develop/starlink-tracker/tracker.py collect
TimeoutStartSec=180
NoNewPrivileges=true
UMask=0077
UNIT
cat > "$unit_dir/starlink-collector.timer" <<'UNIT'
[Unit]
Description=Check Starlink catalog and orbital elements every two hours
[Timer]
OnStartupSec=2min
OnUnitActiveSec=2h
AccuracySec=1min
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now starlink-tracker.service starlink-collector.timer
systemctl --user restart starlink-tracker.service
systemctl --user start starlink-collector.service || { echo 'Collector needs attention; inspect journalctl --user -u starlink-collector.service.'; exit 1; }
systemctl --user is-active starlink-tracker.service starlink-collector.timer
"$target_dir/.venv/bin/python" -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8765/api/status'); print('Dashboard HTTP', r.status)"
touch "$target_dir/.installed"
echo 'Open http://localhost:8765'
