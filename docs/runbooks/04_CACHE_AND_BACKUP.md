# Runbook 4 — Clearing Disk Cache, Quotas & Backup / Restore

## 1. Disk Quota & Automatic Eviction
CLIPPER stores job workspaces under `data/jobs/<job_id>/` and intermediate proxy/audio artifacts under `data/jobs/<job_id>/work/`.
- Configure retention in `.env`:
  ```dotenv
  CLIPPER_MAX_DISK_GB=50
  CLIPPER_JOB_RETENTION_DAYS=14
  ```
- Trigger on-demand maintenance via Admin API or UI:
  ```bash
  curl -X POST http://localhost:8000/api/v1/admin/maintenance \
    -H "Content-Type: application/json" \
    -d '{"action": "cleanup_temp"}'
  ```

## 2. Backup & Restore (`scripts/backup_restore.py`)
Create a timestamped `.tar.gz` backup of the SQLite database (`data/clipper.db`), custom templates (`assets/templates_v2/`), and `.clipper-state/`:

```bash
# Create backup archive
.venv/bin/python scripts/backup_restore.py backup --out backups/clipper-backup.tar.gz

# Restore from backup archive
.venv/bin/python scripts/backup_restore.py restore --archive backups/clipper-backup.tar.gz
```
