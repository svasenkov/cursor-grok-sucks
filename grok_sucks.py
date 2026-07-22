#!/usr/bin/env python
"""Keep Cursor Grok* models out of Cursor.

1. Scrubs local state.vscdb preferences / catalog cache.
2. Patches Cursor workbench JS so Grok* is filtered from Settings / picker
   (disk-only writes do not update the live UI — Cursor keeps state in RAM).

Workaround for:
https://forum.cursor.com/t/grok-re-enables-itself-after-being-disabled-in-settings/165894

Unofficial. Not affiliated with Cursor or xAI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

STORAGE_KEY = (
    "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl"
    ".persistentStorage.applicationUser"
)

PATCH_MARKER = "/*grok-sucks*/"

# Visibility helper used by Settings → Models and the model picker.
# Desktop: e.filter(...); Glass: t.filter(...) — only the predicate differs in context.
FILTER_PRED_OLD = 'r=>!i||r.name!=="default"'
FILTER_PRED_NEW = (
    'r=>(!i||r.name!=="default")&&!/^grok/i.test(r.name||"")' + PATCH_MARKER
)

# Catalog loader: availableDefaultModels2??[]).map(IDENT)
CATALOG_MAP_RE = re.compile(
    r"(availableDefaultModels2\?\?\[\]\))\.map\(([A-Za-z_$][\w$]*)\)"
)
CATALOG_MAP_PATCHED_RE = re.compile(
    r"(availableDefaultModels2\?\?\[\]\))\.map\(([A-Za-z_$][\w$]*)\)"
    r"\.filter\(_gs=>!/\^grok/i\.test\(_gs\.name\|\|\"\"\)\)"
    + re.escape(PATCH_MARKER)
)


def is_grok(model_id: str) -> bool:
    return bool(model_id) and model_id.lower().startswith("grok")


def state_db_path() -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        )
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise SystemExit("APPDATA is not set")
        return Path(appdata) / "Cursor/User/globalStorage/state.vscdb"
    return Path.home() / ".config/Cursor/User/globalStorage/state.vscdb"


def workbench_paths() -> list[Path]:
    names = ("workbench.desktop.main.js", "workbench.glass.main.js")
    roots: list[Path] = []
    if sys.platform == "darwin":
        roots.append(Path("/Applications/Cursor.app/Contents/Resources/app"))
        roots.append(
            Path.home() / "Applications/Cursor.app/Contents/Resources/app"
        )
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            roots.append(Path(local) / "Programs/cursor/resources/app")
            roots.append(Path(local) / "Programs/Cursor/resources/app")
    else:
        roots.extend(
            [
                Path("/usr/share/cursor/resources/app"),
                Path("/usr/lib/cursor/resources/app"),
                Path("/opt/cursor/resources/app"),
                Path.home() / ".local/share/cursor/resources/app",
            ]
        )

    found: list[Path] = []
    for root in roots:
        for name in names:
            path = root / "out/vs/workbench" / name
            if path.is_file() and path not in found:
                found.append(path)
    return found


def _catalog_repl(match: re.Match[str]) -> str:
    return (
        f"{match.group(1)}.map({match.group(2)})"
        f'.filter(_gs=>!/^grok/i.test(_gs.name||"")){PATCH_MARKER}'
    )


def patch_file(path: Path, *, undo: bool = False, dry_run: bool = False) -> list[str]:
    actions: list[str] = []
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    original = text

    if undo:
        if PATCH_MARKER not in text:
            return []
        text = text.replace(FILTER_PRED_NEW, FILTER_PRED_OLD)
        text = CATALOG_MAP_PATCHED_RE.sub(r"\1.map(\2)", text)
        text = text.replace(
            f'.filter(_gs=>!/^grok/i.test(_gs.name||"")){PATCH_MARKER}',
            "",
        )
        if text == original:
            return []
        actions.append(f"unpatch {path.name}")
    else:
        if FILTER_PRED_NEW in text and CATALOG_MAP_PATCHED_RE.search(text):
            return []

        if FILTER_PRED_OLD in text:
            text = text.replace(FILTER_PRED_OLD, FILTER_PRED_NEW)
            actions.append(f"patch filter {path.name}")
        elif FILTER_PRED_NEW not in text:
            actions.append(f"skip filter {path.name} (pattern not found)")

        if not CATALOG_MAP_PATCHED_RE.search(text):
            new_text, n = CATALOG_MAP_RE.subn(_catalog_repl, text, count=1)
            if n:
                text = new_text
                actions.append(f"patch catalog {path.name}")
            elif CATALOG_MAP_RE.search(original):
                actions.append(f"skip catalog {path.name} (pattern not found)")

        if text == original:
            return [a for a in actions if a.startswith("skip")] or []

    if dry_run:
        return actions

    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return actions


def apply_workbench_patch(*, undo: bool = False, dry_run: bool = False) -> list[str]:
    paths = workbench_paths()
    if not paths:
        return ["workbench: not found (is Cursor installed?)"]

    actions: list[str] = []
    for path in paths:
        try:
            actions.extend(patch_file(path, undo=undo, dry_run=dry_run))
        except PermissionError:
            actions.append(f"permission denied: {path}")
        except OSError as exc:
            actions.append(f"error {path.name}: {exc}")
    return actions


def workbench_status() -> None:
    paths = workbench_paths()
    if not paths:
        print("workbench: (not found)")
        return
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        patched = PATCH_MARKER in text
        print(f"workbench: {'PATCHED' if patched else 'not patched'}  {path}")


def pick_fallback(ai: dict, preferred: str | None) -> str:
    enabled = [m for m in (ai.get("modelOverrideEnabled") or []) if not is_grok(m)]
    if preferred and not is_grok(preferred):
        if preferred in enabled or preferred == "default":
            return preferred
        if preferred:
            return preferred
    for candidate in ("composer-2.5", "claude-opus-4-8", "default"):
        if candidate in enabled or candidate == "default":
            return candidate
    return enabled[0] if enabled else "default"


def _drop_grok_ids(items: list | None) -> tuple[list, list[str]]:
    kept: list = []
    removed: list[str] = []
    for item in items or []:
        if is_grok(str(item)):
            removed.append(str(item))
        else:
            kept.append(item)
    return kept, removed


def scrub_ai_settings(ai: dict, fallback: str) -> list[str]:
    actions: list[str] = []

    enabled, removed_on = _drop_grok_ids(ai.get("modelOverrideEnabled"))
    disabled, removed_off = _drop_grok_ids(ai.get("modelOverrideDisabled"))
    if removed_on or removed_off:
        ai["modelOverrideEnabled"] = enabled
        ai["modelOverrideDisabled"] = disabled
        for m in removed_on + removed_off:
            actions.append(f"override remove {m}")

    no_switch, removed_ns = _drop_grok_ids(ai.get("modelsWithNoDefaultSwitch"))
    if removed_ns:
        ai["modelsWithNoDefaultSwitch"] = no_switch
        actions.append(f"modelsWithNoDefaultSwitch: -{','.join(removed_ns)}")

    last_used = ai.get("modelLastUsedAt")
    if isinstance(last_used, dict):
        drop = [k for k in last_used if is_grok(k)]
        if drop:
            for k in drop:
                del last_used[k]
            actions.append(f"modelLastUsedAt: -{','.join(drop)}")

    params = ai.get("modelParameterPreferences")
    if isinstance(params, dict):
        drop = [k for k in params if is_grok(k)]
        if drop:
            for k in drop:
                del params[k]
            actions.append(f"modelParameterPreferences: -{','.join(drop)}")

    prev = ai.get("previousModelBeforeDefault")
    if isinstance(prev, dict):
        for surface, value in list(prev.items()):
            if not isinstance(value, str):
                continue
            parts = [p.strip() for p in value.split(",")]
            kept = [p for p in parts if not is_grok(p)]
            if kept != parts:
                prev[surface] = ",".join(kept) if kept else fallback
                actions.append(f"previousModelBeforeDefault.{surface} scrubbed")

    model_config = ai.get("modelConfig") or {}
    for surface, cfg in model_config.items():
        if not isinstance(cfg, dict):
            continue
        name = cfg.get("modelName") or ""
        selected = cfg.get("selectedModels") or []
        surface_changed = False

        if is_grok(name):
            cfg["modelName"] = fallback
            surface_changed = True

        new_selected = []
        for item in selected:
            mid = (item or {}).get("modelId", "")
            if is_grok(mid):
                new_selected.append({"modelId": fallback, "parameters": []})
                surface_changed = True
            else:
                new_selected.append(item)

        if surface_changed:
            if not cfg.get("modelName") or is_grok(cfg.get("modelName") or ""):
                cfg["modelName"] = fallback
            if not new_selected:
                new_selected = [{"modelId": fallback, "parameters": []}]
            cfg["selectedModels"] = new_selected
            model_config[surface] = cfg
            actions.append(f"{surface}: -> {fallback}")

    ai["modelConfig"] = model_config
    return actions


def catalog_backup_path() -> Path:
    return Path.home() / ".grok-sucks" / "catalog-entries.json"


def backup_catalog_entries(entries: list) -> None:
    if not entries:
        return
    path = catalog_backup_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    by_name: dict[str, dict] = {}
    if path.is_file():
        try:
            for item in json.loads(path.read_text(encoding="utf-8")):
                if isinstance(item, dict) and item.get("name"):
                    by_name[str(item["name"])] = item
        except (json.JSONDecodeError, OSError):
            pass
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name"):
            by_name[str(entry["name"])] = entry
    path.write_text(
        json.dumps(list(by_name.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def scrub_catalog(root: dict) -> list[str]:
    catalog = root.get("availableDefaultModels2")
    if not isinstance(catalog, list):
        return []

    kept = []
    removed_entries: list = []
    removed: list[str] = []
    for entry in catalog:
        name = ""
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("serverModelName") or "")
        elif isinstance(entry, str):
            name = entry
        if is_grok(name):
            removed.append(name)
            if isinstance(entry, dict):
                removed_entries.append(entry)
        else:
            kept.append(entry)

    if not removed:
        return []

    backup_catalog_entries(removed_entries)
    root["availableDefaultModels2"] = kept
    return [f"catalog remove {m}" for m in removed]


def restore_catalog(root: dict, model_ids: list[str]) -> list[str]:
    catalog = root.get("availableDefaultModels2")
    if not isinstance(catalog, list):
        catalog = []
        root["availableDefaultModels2"] = catalog

    present = {
        str(e.get("name") or e.get("serverModelName") or "")
        for e in catalog
        if isinstance(e, dict)
    }
    actions: list[str] = []
    backup: list = []
    path = catalog_backup_path()
    if path.is_file():
        try:
            backup = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            backup = []

    for model_id in model_ids:
        if model_id in present:
            continue
        entry = next(
            (e for e in backup if isinstance(e, dict) and e.get("name") == model_id),
            None,
        )
        if entry:
            catalog.append(entry)
            actions.append(f"catalog restore {model_id}")
        else:
            actions.append(f"catalog missing {model_id} (refresh Models in Cursor)")

    root["availableDefaultModels2"] = catalog
    return actions


def restore_ai_settings(ai: dict, model_id: str, *, select: bool) -> list[str]:
    actions: list[str] = []
    enabled = list(ai.get("modelOverrideEnabled") or [])
    disabled = list(ai.get("modelOverrideDisabled") or [])

    if model_id in disabled:
        disabled = [m for m in disabled if m != model_id]
        actions.append(f"override undisable {model_id}")
    if model_id not in enabled:
        enabled.append(model_id)
        actions.append(f"override enable {model_id}")
    ai["modelOverrideEnabled"] = enabled
    ai["modelOverrideDisabled"] = disabled

    if select:
        model_config = ai.get("modelConfig") or {}
        composer = model_config.get("composer")
        if isinstance(composer, dict):
            composer["modelName"] = model_id
            composer["selectedModels"] = [{"modelId": model_id, "parameters": []}]
            model_config["composer"] = composer
            ai["modelConfig"] = model_config
            actions.append(f"composer: -> {model_id}")
    return actions


def scrub_feature_configs(root: dict, fallback: str) -> list[str]:
    actions: list[str] = []
    fmc = root.get("featureModelConfigs")
    if not isinstance(fmc, dict):
        return actions

    def scrub_list(lst: object) -> tuple[list, bool]:
        if not isinstance(lst, list):
            return [], False
        new = [m for m in lst if not is_grok(str(m))]
        return new, new != lst

    for key, cfg in fmc.items():
        if key == "subagentModels" and isinstance(cfg, dict):
            for sub_name, sub_cfg in cfg.items():
                if not isinstance(sub_cfg, dict):
                    continue
                dm = sub_cfg.get("defaultModel") or ""
                if is_grok(dm):
                    sub_cfg["defaultModel"] = fallback
                    actions.append(f"subagent {sub_name}: {dm} -> {fallback}")
                fl, changed = scrub_list(sub_cfg.get("fallbackModels"))
                if changed:
                    sub_cfg["fallbackModels"] = fl
                    actions.append(f"subagent {sub_name}: fallbackModels scrubbed")
            continue
        if not isinstance(cfg, dict):
            continue
        dm = cfg.get("defaultModel") or ""
        if is_grok(dm):
            cfg["defaultModel"] = fallback
            actions.append(f"{key}: defaultModel {dm} -> {fallback}")
        for field in ("fallbackModels", "bestOfNDefaultModels"):
            fl, changed = scrub_list(cfg.get(field))
            if changed:
                cfg[field] = fl
                actions.append(f"{key}: {field} scrubbed")
    return actions


def load_ai(db: Path) -> dict:
    con = sqlite3.connect(str(db), timeout=10)
    try:
        row = con.execute(
            "SELECT value FROM ItemTable WHERE key = ?", (STORAGE_KEY,)
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"Missing storage key in {db}")
    root = json.loads(row[0])
    ai = root.get("aiSettings")
    if not isinstance(ai, dict):
        raise SystemExit("aiSettings missing in Cursor storage")
    return root


def apply_restore(
    db: Path,
    model_id: str,
    *,
    select: bool,
    dry_run: bool,
) -> list[str]:
    if not db.is_file():
        raise SystemExit(f"Cursor state DB not found: {db}")

    last_err: Exception | None = None
    for _ in range(5):
        try:
            con = sqlite3.connect(str(db), timeout=10)
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT value FROM ItemTable WHERE key = ?", (STORAGE_KEY,)
                ).fetchone()
                if not row:
                    raise SystemExit(f"Missing storage key in {db}")

                root = json.loads(row[0])
                ai = root.get("aiSettings")
                if not isinstance(ai, dict):
                    raise SystemExit("aiSettings missing in Cursor storage")

                actions = restore_ai_settings(ai, model_id, select=select)
                actions.extend(restore_catalog(root, [model_id]))

                if not actions:
                    con.rollback()
                    return []

                if dry_run:
                    con.rollback()
                    return actions

                root["aiSettings"] = ai
                root["SPECIAL_KEY_lastUpdatedTimeInUnixSeconds"] = time.time()
                payload = json.dumps(root, ensure_ascii=False, separators=(",", ":"))
                con.execute(
                    "UPDATE ItemTable SET value = ? WHERE key = ?",
                    (payload, STORAGE_KEY),
                )
                con.commit()
                return actions
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            last_err = e
            time.sleep(0.2)
    raise SystemExit(f"SQLite error after retries: {last_err}")


def apply(db: Path, fallback_pref: str | None, hard: bool, dry_run: bool) -> list[str]:
    if not db.is_file():
        raise SystemExit(f"Cursor state DB not found: {db}")

    last_err: Exception | None = None
    for _ in range(5):
        try:
            con = sqlite3.connect(str(db), timeout=10)
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT value FROM ItemTable WHERE key = ?", (STORAGE_KEY,)
                ).fetchone()
                if not row:
                    raise SystemExit(f"Missing storage key in {db}")

                root = json.loads(row[0])
                ai = root.get("aiSettings")
                if not isinstance(ai, dict):
                    raise SystemExit("aiSettings missing in Cursor storage")

                fallback = pick_fallback(ai, fallback_pref)
                actions = scrub_ai_settings(ai, fallback)
                actions.extend(scrub_catalog(root))
                if hard:
                    actions.extend(scrub_feature_configs(root, fallback))

                if not actions:
                    con.rollback()
                    return []

                if dry_run:
                    con.rollback()
                    return actions

                root["aiSettings"] = ai
                root["SPECIAL_KEY_lastUpdatedTimeInUnixSeconds"] = time.time()
                payload = json.dumps(root, ensure_ascii=False, separators=(",", ":"))
                con.execute(
                    "UPDATE ItemTable SET value = ? WHERE key = ?",
                    (payload, STORAGE_KEY),
                )
                con.commit()
                return actions
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            last_err = e
            time.sleep(0.2)
    raise SystemExit(f"SQLite error after retries: {last_err}")


def _catalog_grok_names(root: dict) -> list[str]:
    catalog = root.get("availableDefaultModels2") or []
    names: list[str] = []
    for entry in catalog:
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("serverModelName") or "")
        else:
            name = str(entry)
        if is_grok(name):
            names.append(name)
    return names


def print_status(db: Path) -> None:
    root = load_ai(db)
    ai = root["aiSettings"]
    enabled = [m for m in (ai.get("modelOverrideEnabled") or []) if is_grok(m)]
    disabled = [m for m in (ai.get("modelOverrideDisabled") or []) if is_grok(m)]
    catalog = _catalog_grok_names(root)
    composer = (ai.get("modelConfig") or {}).get("composer", {}).get("modelName")
    print(f"db: {db}")
    print(f"grok in catalog: {catalog or '(none)'}")
    print(f"grok enabled:    {enabled or '(none)'}")
    print(f"grok disabled:   {disabled or '(none)'}")
    print(f"composer model:  {composer}")
    workbench_status()


def _log(actions: list[str]) -> None:
    if not actions:
        return
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] " + "; ".join(actions), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove Cursor Grok* from local state and the Models UI"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="watch",
        choices=("once", "watch", "status", "patch", "unpatch", "restore"),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="watch poll interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--fallback",
        default="composer-2.5",
        help="model to use when active surface is on Grok (default: composer-2.5)",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="also scrub featureModelConfigs fallbacks / subagent defaults",
    )
    parser.add_argument(
        "--model",
        default="grok-4.5",
        help="grok model id for restore (default: grok-4.5)",
    )
    parser.add_argument(
        "--select",
        action="store_true",
        help="with restore: set composer to --model",
    )
    parser.add_argument(
        "--no-patch",
        action="store_true",
        help="with once/watch: do not patch workbench; with restore: skip unpatch",
    )
    parser.add_argument("--dry-run", action="store_true", help="print actions only")
    parser.add_argument("--db", type=Path, default=None, help="override path to state.vscdb")
    args = parser.parse_args()
    db = args.db or state_db_path()

    if args.command == "status":
        print_status(db)
        return

    if args.command == "patch":
        actions = apply_workbench_patch(dry_run=args.dry_run)
        _log(actions or ["workbench already patched"])
        if actions and not args.dry_run and not any("not found" in a for a in actions):
            print("Restart Cursor (or Developer: Reload Window) for the UI list to update.")
        return

    if args.command == "unpatch":
        actions = apply_workbench_patch(undo=True, dry_run=args.dry_run)
        _log(actions or ["workbench was not patched"])
        if actions and not args.dry_run:
            print("Restart Cursor (or Developer: Reload Window) to restore the stock UI.")
        return

    if args.command == "restore":
        actions: list[str] = []
        if not args.no_patch:
            actions.extend(apply_workbench_patch(undo=True, dry_run=args.dry_run))
        actions.extend(
            apply_restore(db, args.model, select=args.select, dry_run=args.dry_run)
        )
        _log(actions or ["already restored"])
        if not args.dry_run:
            print(
                "Restart Cursor (or Developer: Reload Window) so Grok shows in Settings again."
            )
            if not catalog_backup_path().is_file():
                print(
                    "Tip: if Grok is missing from the list, hit refresh in Settings → Models "
                    "or open Cursor once without watch running."
                )
        return

    def run_state() -> list[str]:
        return apply(db, args.fallback, args.hard, args.dry_run)

    def run_all() -> list[str]:
        actions = run_state()
        if not args.no_patch:
            # Re-apply patch if a Cursor update overwrote workbench files.
            actions.extend(apply_workbench_patch(dry_run=args.dry_run))
        return actions

    if args.command == "once":
        actions = run_all()
        _log(actions)
        if not actions:
            print("already clean")
        elif not args.no_patch and any(a.startswith("patch ") for a in actions):
            print("Restart Cursor (or Developer: Reload Window) for the UI list to update.")
        return

    print(
        f"watching {db} every {args.interval}s "
        f"(fallback={args.fallback}, hard={args.hard}, patch={not args.no_patch})",
        flush=True,
    )
    if not args.no_patch:
        # Patch once at start; watch keeps state clean and re-patches after updates.
        _log(apply_workbench_patch(dry_run=args.dry_run) or ["workbench already patched"])
        print(
            "If Grok is still in Settings, restart Cursor once so the workbench patch loads.",
            flush=True,
        )
    while True:
        try:
            _log(run_all())
        except Exception as exc:  # noqa: BLE001 — keep watcher alive
            print(f"[error] {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
