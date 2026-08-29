# 周期备份（#156）

先手动跑通 `kairo backup push REMOTE`，再启用 timer。

1. 把 `kairo-backup@.service` / `kairo-backup@.timer` 拷到 `/etc/systemd/system/`。
2. 改 service：`User`、`ExecStart` 里的 kairo 路径（uv tool 常见为 `%h/.local/bin/kairo`）。
3. 设置 `Environment=KAIRO_SERVE_ROOT=/path/to/serve-root`，或设 `WorkingDirectory` 为 serve root。
4. `systemctl enable --now kairo-backup@<remote>.timer`

周期只改 timer 的 `OnCalendar`。Kairo 本身不解析 cron。
