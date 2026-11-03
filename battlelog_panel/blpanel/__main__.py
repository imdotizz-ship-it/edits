"""Command line entry point: python -m blpanel <command>."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .auth import new_token
from .config import load_settings
from .connector import ConnectorError
from .parser import ProfileParser
from .redact import redact


def _panel():
    from .server import Panel
    return Panel(load_settings())


def cmd_serve(args) -> int:
    import uvicorn
    from .server import Panel, create_app
    settings = load_settings()
    panel = Panel(settings)
    if not panel.auth.configured and not args.insecure_dev:
        print("BLPANEL_ADMIN_TOKEN_SHA256 is not set. Run `python -m blpanel create-token` first, or use "
              "--insecure-dev (test environment only, binds to 127.0.0.1).", file=sys.stderr)
        return 2
    host = "127.0.0.1" if args.insecure_dev else args.host
    app = create_app(panel, insecure_dev=args.insecure_dev)
    print(f"Battlelog panel [{settings.environment}] on http://{host}:{args.port}  db={settings.db_path}")
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


def cmd_create_token(args) -> int:
    token, digest = new_token()
    print("Keep this token secret (it is shown only once):\n  " + token)
    print(f"\nPut the digest in the environment:\n  BLPANEL_{args.role.upper()}_TOKEN_SHA256={digest}")
    return 0


def cmd_probe(args) -> int:
    """Validate the endpoint catalogue against the live site and save evidence."""
    panel = _panel()
    c, parser = panel.connector, ProfileParser()
    report = {"probed_at": datetime.now(timezone.utc).isoformat(), "base_url": panel.settings.base_url,
              "game": panel.settings.game, "steps": []}
    out_dir = panel.settings.capture_dir / datetime.now(timezone.utc).strftime("probe-%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    def step(name, fn):
        try:
            r = fn()
        except ConnectorError as e:
            report["steps"].append({"resource": name, "ok": False, **e.to_dict()})
            return None
        (out_dir / f"{name}.json").write_text(json.dumps(redact({"meta": r.meta(), "body": r.body}), indent=2))
        report["steps"].append({"resource": name, "ok": True, **r.meta()})
        return r

    persona_id, platform, name = args.persona_id, args.platform, args.username
    if args.username:
        ident = step("identity", lambda: c.fetch_identity(args.username))
        if ident:
            parsed = parser.parse_identity(ident)
            report["identity"] = parsed["user"]
            report["soldiers"] = parsed["soldiers"]
            sel = [s for s in parsed["soldiers"] if s["platform"] == platform] or parsed["soldiers"]
            if sel and not persona_id:
                persona_id, platform, name = sel[0]["persona_id"], sel[0]["platform"] or platform, sel[0]["persona_name"]
    if persona_id:
        responses = [r for r in (
            step("overview", lambda: c.fetch_overview(persona_id, platform)),
            step("detailed", lambda: c.fetch_detailed(persona_id, platform)),
            step("dogtags", lambda: c.fetch_dogtags(persona_id, name or "", platform)) if name else None,
        ) if r]
        if any(r.resource in ("overview", "detailed") for r in responses):
            prof = parser.parse_profile(responses, {"persona_id": persona_id, "platform": platform,
                                                    "persona_name": name})
            report["stat_mapping"] = {k: {"origin": v["origin"], "source": v.get("source")}
                                      for k, v in prof["stats"].items()}
            report["unmapped_numeric_fields"] = sorted(prof["extra_stats"])
            report["dogtags_parsed"] = None if prof["dogtags"] is None else len(prof["dogtags"])
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nEvidence saved in {out_dir}", file=sys.stderr)
    return 0 if all(s["ok"] for s in report["steps"]) and report["steps"] else 1


def cmd_resolve(args) -> int:
    try:
        print(json.dumps(_panel().sync.resolve(args.username, "cli"), indent=2))
        return 0
    except ConnectorError as e:
        print(json.dumps(e.to_dict(), indent=2), file=sys.stderr)
        return 1


def cmd_sync(args) -> int:
    r = _panel().sync.sync_profile(args.persona_id, args.platform, args.name, None, "manual", "cli")
    print(json.dumps(r, indent=2))
    return 0 if r["status"] == "ok" else 1


def cmd_import(args) -> int:
    r = _panel().sync.import_capture(Path(args.file), args.name, "cli")
    print(json.dumps(r, indent=2))
    return 0 if r["status"] == "ok" else 1


def cmd_export(args) -> int:
    print(_panel().sync.export(args.profile_id))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="blpanel", description="Battlelog Profile Control Panel")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8787)
    s.add_argument("--insecure-dev", action="store_true", help="no login; test environment and localhost only")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("create-token"); s.add_argument("--role", choices=["admin", "viewer"], default="admin")
    s.set_defaults(fn=cmd_create_token)
    s = sub.add_parser("probe", help="check endpoints against the live site and save evidence")
    s.add_argument("--username"); s.add_argument("--persona-id"); s.add_argument("--platform", default="pc")
    s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("resolve"); s.add_argument("username"); s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("sync"); s.add_argument("persona_id"); s.add_argument("--platform", default="pc")
    s.add_argument("--name"); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("import", help="import a HAR or JSON bundle captured in your browser")
    s.add_argument("file"); s.add_argument("--name"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("export"); s.add_argument("profile_id", type=int); s.set_defaults(fn=cmd_export)
    args = p.parse_args(argv)
    if args.cmd == "probe" and not (args.username or args.persona_id):
        p.error("probe needs --username or --persona-id")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
