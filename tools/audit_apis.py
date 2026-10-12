"""Audit addon calls against a local, pinned copy of Blizzard's shipped Lua source.

Usage: python tools/audit_apis.py --source /path/to/wow-ui-source/Interface
       --commit SHA --build '1.60.1 (70338)'
Requires lupa for reading declarative API metadata. No game process access.
"""
import argparse
import json
import re
from pathlib import Path
from lupa.lua51 import LuaRuntime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lua = LuaRuntime()
    lua.execute('APIDocumentation={}; definitions={}; function APIDocumentation:AddDocumentationTable(t) definitions[#definitions+1]=t end')
    functions, events, errors = {}, {}, []
    for path in sorted(args.source.rglob("*Documentation.lua")):
        if "Blizzard_APIDocumentationGenerated" not in str(path):
            continue
        prior = len(lua.globals().definitions)
        try:
            lua.execute(path.read_text())
        except Exception:
            errors.append(path.name)
            continue
        for index in range(prior + 1, len(lua.globals().definitions) + 1):
            definition = lua.globals().definitions[index]
            namespace = definition.Namespace or ""
            if definition.Functions:
                for _, fn in definition.Functions.items():
                    ns = fn.Namespace if fn.Namespace is not None else namespace
                    api = (ns + "." if ns else "") + fn.Name
                    functions[api] = {"source_file": str(path.relative_to(args.source)),
                                      "has_restrictions": bool(fn.HasRestrictions),
                                      "secret_arguments": fn.SecretArguments,
                                      "returns": [v.Name for _, v in (fn.Returns or lua.table()).items()]}
            if definition.Events:
                for _, event in definition.Events.items():
                    if event.LiteralName:
                        events[event.LiteralName] = {"source_file": str(path.relative_to(args.source)),
                            "has_restrictions": bool(event.HasRestrictions),
                            "secret_in_chat_lockdown": bool(event.SecretInChatMessagingLockdown)}
    files = [(p, p.read_text()) for p in args.source.rglob("*.lua") if "Blizzard_APIDocumentationGenerated" not in str(p)]
    source = "\n".join(p.read_text() for p in (root / "addon/ForeverHA").glob("*.lua"))
    used = set(re.findall(r'(?:F\.(?:call|require|resolve)|count|tuple|ids)\(\s*"([\w.]+)"', source))
    used.update(re.findall(r'"(C_[A-Za-z0-9_]+\.[A-Za-z0-9_]+)"', source))
    used.update(["issecretvalue", "canaccesstable", "C_Map.GetPlayerMapPosition"])
    used = {api for api in used if re.match(r"^(?:C_|Get|Is|Can|Unit|Player|BN|Logging|issecretvalue|canaccesstable)", api)}
    entries = {}
    for api in sorted(used):
        metadata = functions.get(api, {})
        usages = [str(p.relative_to(args.source)) for p, content in files if api in content]
        entries[api] = dict(metadata, source_evidence="generated_api" if metadata else
                            "shipped_ui_usage" if usages else "runtime_probe_only",
                            ui_examples=sorted(usages, key=lambda p: ("Camelot" not in p, "Deprecated" in p))[:3])
    registered = set(re.findall(r'"([A-Z][A-Z_0-9]+)"', source))
    registered = {name: events.get(name, {"source_evidence": "runtime_probe_only"}) for name in sorted(registered)
                  if name in events or "_" in name and name not in ("PLAYER", "HELPFUL", "HARMFUL")}
    collectors = re.findall(r'F.add\("([a-z_]+)"', source)
    windows = {"bank": "bank", "recipes": "profession", "mail": "mail", "auctions": "auction",
               "mounts": "MountJournal", "collections": "ToyBox", "appearances": "WardrobeCollectionFrame",
               "achievements": "AchievementFrame"}
    datasets = {}
    matches = list(re.finditer(r'F.add\("([a-z_]+)"', source))
    for index, match in enumerate(matches):
        name = match[1]
        segment = source[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(source)]
        apis = sorted({api for api in entries if '"' + api + '"' in segment})
        if name in ("bank", "bags"):
            apis.extend(api for api in entries if api.startswith("C_Container.") and api not in apis)
        datasets[name] = {"export_route": "saved_variables_on_logout_or_reload", "availability": "runtime_probed",
                          "requires_window": windows.get(name), "apis": sorted(apis),
                          "execution_context": "normal_frame_callback_after_world_ready; no_coroutines",
                          "restriction_policy": "omit_secret_fields; isolate_failed_calls; preserve_previous_observation"}
        if name == "pets":
            datasets[name]["journal_requires_window"] = "PetJournal"
    datasets.update({
        "addon_events": {"export_route": "saved_variables_on_logout_or_reload", "availability": "events_delivered_by_client; secret_fields_omitted", "apis": ["issecretvalue", "canaccesstable"]},
        "combat_log": {"export_route": "game_written_log", "availability": "when_logging_enabled; disk_flush_latency", "apis": ["LoggingCombat"], "restrictions": "unknown_or_advanced_layouts_preserved_raw; statistics_only_for_recognized_layouts"},
        "chat_log": {"export_route": "game_written_log", "availability": "messages_the_game_writes_when_logging_enabled", "apis": ["LoggingChat"]},
    })
    result = {"source_repository": "https://github.com/Gethe/wow-ui-source", "branch": "forever",
              "commit": args.commit, "build": args.build, "interface": 16001,
              "note": "Static evidence does not confirm feature availability or load conditions in the live client. Runtime probes remain authoritative.",
              "collectors": sorted(collectors), "datasets": datasets, "apis": entries, "events": registered,
              "metadata_files_not_evaluated": errors}
    target = root / "docs/api-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"Audited {len(entries)} APIs and {len(registered)} events for {len(collectors)} collectors")


if __name__ == "__main__":
    main()
