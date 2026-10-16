"""Read-only investigation helper; does not modify the game."""
from pathlib import Path
import argparse
import json
from collections import Counter

from hotload import APP, DEFAULT_GAME, auto_source
from sekiro_hotload.engine import Engine, atomic_json, sha256
from sekiro_hotload.runtime import build_runtime_image, inspect_table
from sekiro_hotload.formats import Decoder, load_params
from sekiro_hotload.winmem import ProcessMemory, file_version, find_process


def main():
    parser = argparse.ArgumentParser(description="只读检查只狼运行时参数结构，不向游戏写入任何数据")
    parser.add_argument("--plan", action="store_true", help="离线构造新镜像并报告行集合差异；不会应用到游戏")
    args = parser.parse_args()
    game = Path(DEFAULT_GAME)
    exe = game / "sekiro.exe"
    profile = json.loads((APP / "profile.json").read_text(encoding="utf-8"))
    if file_version(exe) != profile["exeVersion"] or sha256(exe) != profile["expectedExeSha256"]:
        raise RuntimeError("EXE 身份不匹配")
    pid = find_process(exe)
    if pid is None:
        raise RuntimeError("游戏未运行")
    mem = ProcessMemory(pid, writable=False)
    try:
        engine = Engine(mem, profile, APP / "state", profile["expectedExeSha256"])
        tables, failures = [], []
        for name in engine.offsets:
            try:
                tables.append(inspect_table(engine, name))
            except Exception as error:
                failures.append({"name": name, "reason": str(error)})
        report = {"read_only": True, "pid": pid, "identity": mem.identity,
                  "tables": tables, "failures": failures}
        if args.plan:
            params = load_params(auto_source(game), Decoder(game), set(engine.offsets))
            changes, images, rejected = [], 0, []
            for item in tables:
                name = item["name"]
                if name not in params:
                    continue
                try:
                    param = params[name]
                    live = engine.table(param)
                    metadata = mem.read(live.base - 16, 16)
                    if metadata.hex() != item["metadata_hex"]:
                        raise RuntimeError("采样期间参数元数据变化")
                    image = build_runtime_image(param, live.header, metadata)
                    added = Counter(r.id for r in param.rows) - Counter(live.ids)
                    removed = Counter(live.ids) - Counter(r.id for r in param.rows)
                    images += 1
                    if added or removed:
                        changes.append({"name": name, "added_ids": sorted(added), "removed_ids": sorted(removed),
                                        "old_count": len(live.ids), "new_count": image.row_count,
                                        "image_bytes": len(image.allocation)})
                except Exception as error:
                    rejected.append({"name": name, "reason": str(error)})
            report["offline_plan"] = {"modelled_images": images, "changes": changes, "rejected": rejected,
                                      "applied_to_game": False}
        target = APP / "state" / "structural-readonly.json"
        atomic_json(target, report)
        print(json.dumps({"validated_tables": len(tables), "failures": failures,
                          "example": next((t for t in tables if t["name"] == "AtkParam_Pc"), None),
                          "offline_plan": report.get("offline_plan"),
                          "report": str(target)}, ensure_ascii=False, indent=2))
        return 0 if not failures else 2
    finally:
        mem.close()


if __name__ == "__main__":
    raise SystemExit(main())
