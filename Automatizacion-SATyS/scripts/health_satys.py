#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/estado_actual.json")
if not path.exists():
    print(f"SIN ESTADO: {path}")
    raise SystemExit(1)

data = json.loads(path.read_text(encoding="utf-8"))
updated = data.get("updated_at") or ""
running = data.get("running")
stage = data.get("stage")
msg = data.get("mensaje", "")
print(f"running={running} stage={stage} updated_at={updated} pid={data.get('pid')} host={data.get('hostname')}")
if msg:
    print(f"mensaje={msg}")
try:
    dt = datetime.fromisoformat(updated)
    age = (datetime.now() - dt).total_seconds()
    print(f"edad_estado_seg={int(age)}")
    if running and age > 1200:
        print("ALERTA: proceso marcado como running pero estado sin actualizar por más de 20 min")
        raise SystemExit(2)
except Exception:
    pass
