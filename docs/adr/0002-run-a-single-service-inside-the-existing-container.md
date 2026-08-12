---
status: superseded by 0003
---

# Run one long-lived service inside the existing container

Only container-internal access is available, so the application will run one Python service that serves the static website and schedules the daily 08:00 Asia/Shanghai pipeline. The operator accepts manually restarting this service after the container stops; host cron, Docker port mappings, and Supervisor are outside this project's control.
