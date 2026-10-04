import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
from app.database import migrate
from app.services.backup import create_backup
from app.database import engine
from app.utils.process_lock import process_guard

if __name__=='__main__':
    with process_guard(engine):
        from app.services.backup import recover_pending_restore, backup_before_upgrade
        recover_pending_restore()
        backup_before_upgrade()
        migrate()
        print('备份完成：',create_backup())
