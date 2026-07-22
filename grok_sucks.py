#!/usr/bin/env python
"""Keep Cursor Grok* models out of local Cursor state.

Removes Grok from the model catalog (availableDefaultModels2) and preference
overrides. Workaround for Cursor re-enabling / nudging Grok:
https://forum.cursor.com/t/grok-re-enables-itself-after-being-disabled-in-settings/165894

Unofficial. Reads/writes Cursor's local SQLite state DB only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

STORAGE_KEY = (
    "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl"
    ".persistentStorage.applicationUser"
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
    """Return (kept, removed_grok_ids)."""
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
            # may be comma-separated ensemble
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


def scrub_catalog(root: dict) -> list[str]:
    """Remove Grok* entries from availableDefaultModels2 (Settings / picker catalog)."""
    catalog = root.get("availableDefaultModels2")
    if not isinstance(catalog, list):
        return []

    kept = []
    removed: list[str] = []
    for entry in catalog:
        name = ""
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("serverModelName") or "")
        elif isinstance(entry, str):
            name = entry
        if is_grok(name):
            removed.append(name)
        else:
            kept.append(entry)

    if not removed:
        return []

    root["availableDefaultModels2"] = kept
    return [f"catalog remove {m}" for m in removed]


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Disable Cursor Grok* models in local Cursor state"
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="watch",
        choices=("once", "watch", "status"),
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
    parser.add_argument("--dry-run", action="store_true", help="print actions only")
    parser.add_argument("--db", type=Path, default=None, help="override path to state.vscdb")
    args = parser.parse_args()
    db = args.db or state_db_path()

    if args.command == "status":
        print_status(db)
        return

    def run() -> list[str]:
        actions = apply(db, args.fallback, args.hard, args.dry_run)
        if actions:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] " + "; ".join(actions), flush=True)
        return actions

    if args.command == "once":
        actions = run()
        if not actions:
            print("already clean")
        return

    print(
        f"watching {db} every {args.interval}s "
        f"(fallback={args.fallback}, hard={args.hard})",
        flush=True,
    )
    while True:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 — keep watcher alive
            print(f"[error] {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
