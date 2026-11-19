"""Local, append-only checkpoints for a Windows Codex project migration."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import migration_ntfs as nt


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def absolute(value):
    path = Path(value)
    if (not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":"
            or path == Path(path.anchor) or any(
                x in (".", "..") or x.endswith((" ", ".")) or Path(x).is_reserved()
                or any(c in '<>:"|?*' or ord(c) < 32 for c in x) for x in path.parts[1:])):
        raise ValueError(f"Expected an absolute path: {value}")
    return path


def store_new(path, data):
    # Windows rename does not replace an existing destination. An interrupted
    # write leaves only an unpublished .partial, never a half-written event.
    temp = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temp, path)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def record(args):
    run = absolute(args.run)
    if args.command == "start":
        old = absolute(args.source)
        new = absolute(args.target)
        if not old.is_dir() or old.resolve(strict=True) != old:
            raise ValueError("Source must be the original physical project directory.")
        if new.resolve() != new:
            raise ValueError("Destination has a redirected ancestor.")
        if not run.parent.is_dir() or run.parent.resolve() != run.parent:
            raise ValueError("Create or select a physical audit parent outside the project.")
        if old == Path(old.anchor) or new == Path(new.anchor):
            raise ValueError("A volume root cannot be a project.")
        backup = old.with_name(old.name + "_迁移备份")
        if os.path.lexists(backup):
            raise FileExistsError(f"Backup name is already occupied: {backup}. Check for a prior run.")
        if os.path.lexists(new):
            raise FileExistsError(f"Destination exists: {new}. Resume its verified run or select a fresh path.")
        roots = tuple(nt.physical(p) for p in (old, new, backup, run))
        for i, left in enumerate(roots):
            for right in roots[i + 1:]:
                if left.is_relative_to(right) or right.is_relative_to(left):
                    raise ValueError("Source, destination, backup and audit must be separate.")
        if not args.project_id.strip():
            raise ValueError("Missing project ID.")
        nt.create_secured(run, old, directory=True, protect=True)
        nt.restore_security(old, run, True)
        store_new(run / "run.json", dict(version=1, project_id=args.project_id,
                  source=str(old), target=str(new), backup=str(backup), started=timestamp()))
    if not run.is_dir() or run.resolve(strict=True) != run:
        raise ValueError("Run directory must be physical.")
    config = read(run / "run.json")
    events = [read(p) for p in sorted(run.glob("event-*.json"))]
    step = 1
    for event in events:
        if event["status"] == "reopened":
            step = event["step"]
        elif event["status"] in ("passed", "not_applicable"):
            step += 1
    if args.command in ("mark", "reopen"):
        if args.command == "reopen":
            if not 1 <= args.step < step:
                raise ValueError("Only an already completed stage can be reopened.")
            step = args.step
        elif step != args.step or not 1 <= step <= 7:
            raise ValueError(f"Expected step {step}; verify prior checkpoints before continuing.")
        if args.status == "not_applicable" and step != 6:
            raise ValueError("Only a confirmed absence of eligible chats at step 6 is not applicable.")
        if not args.evidence.strip() or not args.next_action.strip():
            raise ValueError("Provide an evidence reference and the next action.")
        event = dict(step=step, status=args.status, evidence=args.evidence,
                     next_action=args.next_action, recorded=timestamp())
        store_new(run / f"event-{len(events) + 1:06}.json", event)
        events.append(event)
        if args.status in ("passed", "not_applicable"):
            step += 1
    print(json.dumps(dict(run=str(run), config=config, next_step=step if step <= 7 else None,
                          last_event=events[-1] if events else None), ensure_ascii=True))


def main():
    if os.name != "nt":
        raise SystemExit("This helper targets Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("source")
    start.add_argument("target")
    start.add_argument("--project-id", required=True)
    show = commands.add_parser("show")
    mark = commands.add_parser("mark")
    reopen = commands.add_parser("reopen")
    reopen.set_defaults(status="reopened")
    for command in (start, show, mark, reopen):
        command.add_argument("--run", required=True)
    mark.add_argument("--status", choices=("waiting", "blocked", "passed", "not_applicable"), required=True)
    for command in (mark, reopen):
        command.add_argument("--step", type=int, required=True)
        command.add_argument("--evidence", required=True)
        command.add_argument("--next-action", required=True)
    try:
        record(parser.parse_args())
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Migration checkpoint: {exc}\n")


if __name__ == "__main__":
    main()
