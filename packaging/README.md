# Automatización por sistema operativo

La corrida diaria de aurclips (`aurclips run`) genera los Shorts y los sube en
privado con fecha programada; **YouTube los publica solo**. Lo único que hay que
automatizar es disparar `aurclips run` una vez al día. El comando ya deja su
propio log y se protege contra solapes, así que el scheduler solo lo invoca.

Elige el mecanismo de tu SO:

## Linux — systemd (recomendado) o cron

**systemd (user timer):** copia las unidades, ajusta la ruta del checkout y la
hora, y actívalas:

```bash
mkdir -p ~/.config/systemd/user
cp packaging/systemd/aurclips.service ~/.config/systemd/user/
cp packaging/systemd/aurclips.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aurclips.timer
# para que corra aunque no tengas sesión abierta:
loginctl enable-linger "$USER"
```

Prueba una corrida ya: `systemctl --user start aurclips.service`.

**cron (universal):** `crontab -e` y pega la línea de
[`crontab.example`](crontab.example), ajustando la ruta.

## macOS — launchd

Ajusta `TU_USUARIO` y la ruta en el plist, cópialo y cárgalo:

```bash
cp packaging/launchd/com.aurclips.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aurclips.daily.plist
# probar ya:
launchctl start com.aurclips.daily
```

cron también funciona en macOS ([`crontab.example`](crontab.example)).

## Windows — Programador de tareas

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1 -Hora "03:00"
```

Registra la tarea `aurclips-diario`. Pruébala con
`Start-ScheduledTask -TaskName aurclips-diario`.
