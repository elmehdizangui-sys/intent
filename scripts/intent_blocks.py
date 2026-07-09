#!/usr/bin/env python3
"""
intent_blocks.py — Intent Annotation v1.0

Commands:
  finalize  --plan "<text>" --regions '<json>' --model "<model>" [--prompt "<text>"]
  verify    [path] [--strict]
  list      <file>
  find      [path]
  explain   <file>:<line>
  ack       <id> --region <N> --reason "<why>"
  session-check [--n 5]

Receipt files: .intent/receipts/<id>.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intent_config import (  # noqa: E402
    IntentConfig,
    auto_directive,
    load_config,
    merge,
    save_config,
)

SPEC_VERSION = "1.0"
RECEIPTS_DIR = ".intent/receipts"

COMMENT_STYLES: dict[str, str] = {
    "kt": "//",   "kts": "//",  "java": "//", "js": "//",   "jsx": "//",
    "mjs": "//",  "cjs": "//",  "ts": "//",   "tsx": "//",  "swift": "//",
    "scala": "//","groovy": "//","gradle": "//","dart": "//","cs": "//",
    "go": "//",   "rs": "//",   "c": "//",    "h": "//",    "cpp": "//",
    "cc": "//",   "cxx": "//",  "hpp": "//",  "proto": "//",
    "py": "#",    "rb": "#",    "sh": "#",    "bash": "#",  "zsh": "#",
    "yaml": "#",  "yml": "#",   "toml": "#",
    "sql": "--",  "lua": "--",  "hs": "--",
}

EXTENSIONLESS = {
    "Dockerfile", "Makefile", "GNUmakefile", "Jenkinsfile",
    "Vagrantfile", "Brewfile", "Procfile", "Caddyfile", "Containerfile",
}

SKIP_DIRS = {
    ".git", ".intent", "__pycache__", "node_modules",
    ".pytest_cache", ".mypy_cache", "dist", "build", ".next",
}

IMPORT_PATTERNS = [
    "import ", "from ", "use ", "require(", "require '",
    "#include ", "using ", "import(", "@import"
]


def comment_prefix(path: str) -> str:
    name = Path(path).name
    if name in EXTENSIONLESS:
        return "#"
    ext = Path(path).suffix.lstrip(".")
    return COMMENT_STYLES.get(ext, "//")


def compute_hash(local_intent: str, lines: list[str]) -> str:
    code = "\n".join(line.rstrip() for line in lines)
    data = f"{local_intent}\n{code}".encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_id() -> str:
    return secrets.token_hex(4)


def find_repo_root(start: Path) -> Path:
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists() or (p / ".intent").exists():
            return p
        p = p.parent
    return start.resolve()


def receipts_dir(root: Path) -> Path:
    return root / RECEIPTS_DIR


def read_receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_receipt(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def all_receipts(root: Path) -> list[dict]:
    rd = receipts_dir(root)
    if not rd.exists():
        return []
    results = []
    for f in sorted(rd.glob("*.json")):
        try:
            results.append(read_receipt(f))
        except Exception:
            pass
    return results


def find_markers(path: Path, prefix: str) -> list[tuple[int, str, str]]:
    """Return (0-indexed line number, marker type without colon, local_intent)."""
    results = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return results
    for i, line in enumerate(lines):
        stripped = line.strip()
        for mtype in ("@intent:", "@for:"):
            tag = f"{prefix} {mtype}"
            if stripped.startswith(tag):
                text = stripped[len(tag):].strip()
                results.append((i, mtype.rstrip(":"), text))
                break
    return results


def run_git_command(cmd: list[str], cwd: Path, timeout: int = 30) -> tuple[bool, str]:
    """
    Run git command and return (success, output).
    Includes timeout to prevent hanging on large repos or network issues.
    """
    import subprocess
    try:
        result = subprocess.run(
            cmd, 
            cwd=cwd, 
            capture_output=True, 
            text=True, 
            check=True,
            timeout=timeout
        )
        return (True, result.stdout)
    except subprocess.TimeoutExpired:
        return (False, f"Git command timed out after {timeout}s: {' '.join(cmd)}")
    except subprocess.CalledProcessError as e:
        return (False, e.stderr)
    except Exception as e:
        return (False, str(e))


def detect_regions_from_git_diff(root: Path) -> list[dict]:
    """
    Detect changed regions from git diff.
    Returns list of region dicts with computed hashes.
    """
    # Check if git repo
    if not (root / ".git").exists():
        return []
    
    # Run git diff
    success, output = run_git_command(["git", "diff", "--unified=3", "HEAD"], root)
    if not success:
        return []
    
    if not output.strip():
        return []
    
    regions = []
    current_file = None
    current_hunks = []
    
    for line in output.split("\n"):
        # Parse file headers (more robust parsing)
        if line.startswith("+++"):
            if current_file and current_hunks:
                # Process previous file
                regions.extend(_process_git_file(root, current_file, current_hunks))
            
            # Start new file
            if line.startswith("+++ b/"):
                # Standard format: "+++ b/path/to/file.kt"
                current_file = line[6:]
            elif line == "+++ /dev/null":
                # Deleted file - skip
                current_file = None
            else:
                # Fallback for other formats
                parts = line.split("/", 1)
                if len(parts) == 2:
                    current_file = parts[1]
                else:
                    current_file = None
            current_hunks = []
        
        # Parse hunk headers (@@-old_start,old_count +new_start,new_count@@)
        elif line.startswith("@@"):
            parts = line.split("@@")
            if len(parts) >= 2:
                ranges = parts[1].strip().split()
                for r in ranges:
                    if r.startswith("+"):
                        # Extract start line and count
                        r = r.lstrip("+")
                        if "," in r:
                            start, count = r.split(",")
                            current_hunks.append((int(start), int(count)))
                        else:
                            current_hunks.append((int(r), 1))
    
    # Process last file
    if current_file and current_hunks:
        regions.extend(_process_git_file(root, current_file, current_hunks))
    
    return regions


def _process_git_file(root: Path, file_rel: str, hunks: list[tuple[int, int]]) -> list[dict]:
    """Process a file's hunks into regions."""
    fpath = root / file_rel
    if not fpath.exists():
        return []
    
    try:
        all_lines = fpath.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    
    # Check if file has markers - prefer those
    prefix = comment_prefix(file_rel)
    markers = find_markers(fpath, prefix)
    if markers:
        return []  # Will be handled by marker mode
    
    regions = []
    for i, (start_line, count) in enumerate(hunks):
        # Adjust for 1-indexed git diff
        start_idx = max(0, start_line - 1)
        end_idx = min(len(all_lines), start_idx + count)
        
        # Extract changed lines (up to 3 non-blank for hash)
        code_lines = []
        for j in range(start_idx, end_idx):
            if all_lines[j].strip():
                code_lines.append(all_lines[j].rstrip())
                if len(code_lines) == 3:
                    break
        
        if code_lines:
            # Compute hash with empty intent (git-diff mode)
            computed_hash = compute_hash("", code_lines)
            
            regions.append({
                "role": "primary" if i == 0 else "for",
                "file": file_rel,
                "marker_line_hint": start_idx,
                "anchor_content_hash": computed_hash,
                "local_intent": "",
                "detected_via": "git-diff"
            })
    
    return regions


def cmd_finalize_git_diff(args: argparse.Namespace, plan: str) -> int:
    """Handle finalize in git-diff mode."""
    root = find_repo_root(Path("."))
    
    # Check if git repo
    if not (root / ".git").exists():
        print("ERROR: Not a git repository. Initialize git or use --regions mode.", file=sys.stderr)
        return 1
    
    # Detect regions
    regions = detect_regions_from_git_diff(root)
    
    if not regions:
        print("ERROR: No changes detected via git diff.", file=sys.stderr)
        print("       Make edits first, or use --regions for manual marking.", file=sys.stderr)
        return 1
    
    if len(regions) > 20:
        print(f"WARNING: Detected {len(regions)} regions. Consider marking specific regions manually.", file=sys.stderr)
    
    receipt_id = generate_id()
    receipt = {
        "id": receipt_id,
        "spec_version": SPEC_VERSION,
        "created_at": now_iso(),
        "model": args.model or "",
        "intent": {
            "original_prompt": (args.prompt or "").strip(),
            "plan": plan,
        },
        "regions": regions,
        "validation": {
            "hash_validated": True,
            "auto_hash": True,
            "validation_mode": "git-diff"
        }
    }

    out_path = receipts_dir(root) / f"{receipt_id}.json"
    write_receipt(out_path, receipt)

    try:
        rel = out_path.relative_to(Path.cwd())
    except ValueError:
        rel = out_path

    print(f"\nReceipt {receipt_id} written → {rel}")
    print(f"\nGIT-DIFF MODE:")
    print(f"  ✓ {len(regions)} region(s) detected from git diff")
    print(f"  ✓ {len(regions)} hash(es) computed")
    print(f"  ⓘ No markers inserted (marker-less mode)")
    
    for i, r in enumerate(regions):
        role_marker = "@intent:" if r.get("role") == "primary" else "@for:   "
        print(f"  {role_marker} {r['file']}:{r.get('marker_line_hint', '?')+1}")
    
    print(f"\nNext steps:")
    print(f"  1. Run: python3 scripts/intent_blocks.py verify")
    print(f"  2. Commit receipt alongside code changes")
    
    return 0


def source_files(root: Path) -> list[Path]:
    results = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        name = p.name
        ext = p.suffix.lstrip(".")
        if name in EXTENSIONLESS or ext in COMMENT_STYLES:
            results.append(p)
    return results


def code_lines_after_marker(all_lines: list[str], marker_lineno: int, count: int = 3) -> list[str]:
    result = []
    j = marker_lineno + 1
    while j < len(all_lines) and len(result) < count:
        stripped = all_lines[j].rstrip()
        if stripped:
            result.append(stripped)
        j += 1
    return result


def is_import_line(line: str) -> bool:
    """Check if a line is an import statement."""
    stripped = line.strip()
    return any(stripped.startswith(p) for p in IMPORT_PATTERNS)


def find_marker_in_file(fpath: Path, local_intent: str, hint_line: int, prefix: str, search_radius: int = 20) -> tuple[int, list[str], list[str]] | None:
    """
    Find marker matching local_intent near hint_line.
    Returns (marker_lineno, code_lines, all_lines) or None if not found.
    Search ±search_radius lines from hint_line.
    Returns all_lines to avoid duplicate file reads.
    """
    try:
        all_lines = fpath.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    
    # Try exact hint line first
    if 0 <= hint_line < len(all_lines):
        markers = find_markers(fpath, prefix)
        for lineno, _, text in markers:
            if text == local_intent and lineno == hint_line:
                code = code_lines_after_marker(all_lines, lineno)
                return (lineno, code, all_lines)
    
    # Search within radius
    start = max(0, hint_line - search_radius)
    end = min(len(all_lines), hint_line + search_radius + 1)
    
    markers = find_markers(fpath, prefix)
    for lineno, _, text in markers:
        if text == local_intent and start <= lineno < end:
            code = code_lines_after_marker(all_lines, lineno)
            return (lineno, code, all_lines)
    
    return None


def validate_region(root: Path, region: dict, auto_hash: bool) -> tuple[bool, str, dict]:
    """
    Validate a region: find marker, check import line, compute hash.
    Returns (success, error_msg, updated_region).
    If auto_hash=True, fills in anchor_content_hash.
    Otherwise, validates provided hash matches computed hash.
    """
    file_rel = region.get("file", "")
    fpath = (root / file_rel) if not Path(file_rel).is_absolute() else Path(file_rel)
    
    # Security: prevent path traversal attacks
    try:
        fpath_resolved = fpath.resolve()
        root_resolved = root.resolve()
        if not fpath_resolved.is_relative_to(root_resolved):
            return (False, f"Security error: file path escapes repository: {file_rel}", region)
    except (ValueError, OSError) as e:
        return (False, f"Invalid file path: {file_rel} ({e})", region)
    
    if not fpath.exists():
        return (False, f"File not found: {file_rel}", region)
    
    local_intent = region.get("local_intent", "")
    hint_line = region.get("marker_line_hint", 0)
    prefix = comment_prefix(file_rel)
    
    # Find marker
    result = find_marker_in_file(fpath, local_intent, hint_line, prefix)
    if result is None:
        return (False, f"Marker not found in {file_rel} (intent: '{local_intent}', hint line: {hint_line+1})", region)
    
    marker_lineno, code_lines, all_lines = result
    
    # Check if marker is on import line (using all_lines from find_marker_in_file)
    if marker_lineno + 1 < len(all_lines):
        next_line = all_lines[marker_lineno + 1]
        if is_import_line(next_line):
            return (False, 
                    f"Marker at {file_rel}:{marker_lineno+1} is placed on an import line.\n"
                    f"       SKILL.md rule: 'Never mark import lines.'\n"
                    f"       Move the marker below imports, or remove this marker.",
                    region)
    
    # Compute hash
    computed_hash = compute_hash(local_intent, code_lines)
    
    # Update region with actual line number
    updated_region = region.copy()
    updated_region["marker_line_hint"] = marker_lineno
    
    if auto_hash:
        # Fill in hash
        updated_region["anchor_content_hash"] = computed_hash
        return (True, "", updated_region)
    else:
        # Validate provided hash
        provided_hash = region.get("anchor_content_hash", "")
        if not provided_hash:
            return (False, 
                    f"Missing anchor_content_hash for region in {file_rel}.\n"
                    f"       Either provide the hash or use --auto-hash flag.",
                    region)
        
        if provided_hash != computed_hash:
            return (False,
                    f"Hash mismatch in {file_rel}:{marker_lineno+1}\n"
                    f"       Provided:  {provided_hash}\n"
                    f"       Computed:  {computed_hash}\n"
                    f"       The code after the marker doesn't match the hash you provided.",
                    region)
        
        return (True, "", updated_region)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_finalize(args: argparse.Namespace) -> int:
    plan = (args.plan or "").strip()
    if not plan:
        print("ERROR: --plan is required and must not be empty.", file=sys.stderr)
        print("       Show the plan to the dev and wait for 'go' before calling finalize.", file=sys.stderr)
        return 1

    # Handle git-diff mode
    if getattr(args, 'git_diff', False):
        return cmd_finalize_git_diff(args, plan)
    
    # Handle marker mode
    if not args.regions:
        print("ERROR: --regions is required (or use --git-diff mode).", file=sys.stderr)
        return 1

    try:
        regions = json.loads(args.regions)
    except Exception as e:
        print(f"ERROR: --regions is not valid JSON: {e}", file=sys.stderr)
        return 3

    if not regions:
        print("ERROR: --regions must not be empty. Insert @intent:/@for: markers first.", file=sys.stderr)
        return 1

    root = find_repo_root(Path("."))
    auto_hash = getattr(args, 'auto_hash', False)
    
    # Validate all regions
    validated_regions = []
    validation_errors = []
    
    for i, region in enumerate(regions):
        success, error_msg, updated_region = validate_region(root, region, auto_hash)
        if not success:
            validation_errors.append(f"Region {i} ({region.get('file', '?')}): {error_msg}")
        else:
            validated_regions.append(updated_region)
    
    if validation_errors:
        print("ERROR: Region validation failed:", file=sys.stderr)
        for err in validation_errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    
    receipt_id = generate_id()
    receipt = {
        "id": receipt_id,
        "spec_version": SPEC_VERSION,
        "created_at": now_iso(),
        "model": args.model or "",
        "intent": {
            "original_prompt": (args.prompt or "").strip(),
            "plan": plan,
        },
        "regions": validated_regions,
        "validation": {
            "hash_validated": True,
            "auto_hash": auto_hash,
            "validation_mode": "marker"
        }
    }

    out_path = receipts_dir(root) / f"{receipt_id}.json"
    write_receipt(out_path, receipt)

    try:
        rel = out_path.relative_to(Path.cwd())
    except ValueError:
        rel = out_path

    print(f"\nReceipt {receipt_id} written → {rel}")
    print(f"\nVALIDATION COMPLETED:")
    print(f"  ✓ {len(validated_regions)} region(s) validated")
    print(f"  ✓ {len(validated_regions)} hash(es) computed and verified")
    print(f"  ✓ {len(validated_regions)} import-line check(s) passed")
    
    primary = next((r for r in validated_regions if r.get("role") == "primary"), validated_regions[0])
    print(f"\n  @intent: {primary['file']}:{primary.get('marker_line_hint', '?')+1}")
    for r in validated_regions:
        if r.get("role") == "for":
            print(f"  @for:    {r['file']}:{r.get('marker_line_hint', '?')+1}")
    
    print(f"\nNext steps:")
    print(f"  1. Run: python3 scripts/intent_blocks.py verify")
    print(f"  2. Commit receipt alongside code changes")
    print(f"  3. Include receipt in PR for reviewer context")
    
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    receipts = all_receipts(root)
    if not receipts:
        print("No receipts found.")
        return 0

    has_warn = False
    for receipt in receipts:
        rid = receipt["id"]
        validation_mode = receipt.get("validation", {}).get("validation_mode", "marker")
        
        for i, region in enumerate(receipt.get("regions", [])):
            file_rel = region.get("file", "")
            fpath = (root / file_rel) if not Path(file_rel).is_absolute() else Path(file_rel)

            # Security: prevent path traversal attacks
            try:
                fpath_resolved = fpath.resolve()
                root_resolved = root.resolve()
                if not fpath_resolved.is_relative_to(root_resolved):
                    print(f"ERROR    {rid} region {i}: Security: file path escapes repository: {file_rel}")
                    has_warn = True
                    continue
            except (ValueError, OSError):
                print(f"ERROR    {rid} region {i}: Invalid file path: {file_rel}")
                has_warn = True
                continue

            if args.path and not str(fpath).startswith(str(Path(args.path).resolve())):
                continue

            if not fpath.exists():
                print(f"MISSING  {rid} region {i}: {file_rel}")
                has_warn = True
                continue

            # Handle git-diff mode receipts
            if region.get("detected_via") == "git-diff":
                # For git-diff receipts, just verify file hasn't changed drastically
                # This is less precise than marker-based verification
                try:
                    all_lines = fpath.read_text(encoding="utf-8").splitlines()
                    hint_line = region.get("marker_line_hint", 0)
                    code = code_lines_after_marker(all_lines, hint_line)
                    computed = compute_hash("", code)  # empty intent for git-diff
                    stored = region.get("anchor_content_hash", "")
                    
                    if computed == stored:
                        print(f"OK       {rid} region {i}: {file_rel}:{hint_line + 1} (git-diff)")
                    else:
                        print(f"DRIFT    {rid} region {i}: {file_rel}:{hint_line + 1} (git-diff)")
                        print(f"         Note: git-diff receipts have imprecise drift detection")
                        has_warn = True
                except Exception:
                    print(f"ERROR    {rid} region {i}: {file_rel} (git-diff) - cannot read file")
                    has_warn = True
                continue

            # Handle marker-based receipts
            prefix = comment_prefix(file_rel)
            markers = find_markers(fpath, prefix)
            local_intent = region.get("local_intent", "")

            found_line = None
            for lineno, _, text in markers:
                if text == local_intent:
                    found_line = lineno
                    break

            if found_line is None:
                print(f"MISSING  {rid} region {i}: marker '{local_intent}' not found in {file_rel}")
                has_warn = True
                continue

            all_lines = fpath.read_text(encoding="utf-8").splitlines()
            code = code_lines_after_marker(all_lines, found_line)
            computed = compute_hash(local_intent, code)
            stored = region.get("anchor_content_hash", "")

            acks = region.get("acks", [])
            if computed == stored:
                print(f"OK       {rid} region {i}: {file_rel}:{found_line + 1}")
            elif acks and acks[-1].get("new_hash") == computed:
                print(f"ACKED    {rid} region {i}: {file_rel}:{found_line + 1}")
            else:
                print(f"DRIFT    {rid} region {i}: {file_rel}:{found_line + 1}  → run: ack {rid} --region {i} --reason \"<why>\"")
                has_warn = True

    if args.strict and has_warn:
        return 1
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    fpath = Path(args.file)
    if not fpath.exists():
        print(f"File not found: {args.file}", file=sys.stderr)
        return 1

    prefix = comment_prefix(args.file)
    markers = find_markers(fpath, prefix)
    if not markers:
        print(f"No @intent:/@for: markers in {args.file}")
        return 0

    receipt_by_intent: dict[str, str] = {}
    for r in all_receipts(root):
        for region in r.get("regions", []):
            receipt_by_intent[region.get("local_intent", "")] = r["id"]

    for lineno, mtype, text in markers:
        rid = receipt_by_intent.get(text, "(no receipt)")
        print(f"  {lineno + 1:4d}  @{mtype}: {text}  [{rid}]")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    search_root = Path(args.path).resolve() if args.path else root
    files = source_files(search_root)

    found = False
    for fpath in sorted(files):
        prefix = comment_prefix(str(fpath))
        markers = find_markers(fpath, prefix)
        if markers:
            try:
                rel = fpath.relative_to(root)
            except ValueError:
                rel = fpath
            for lineno, mtype, text in markers:
                print(f"{rel}:{lineno + 1}  @{mtype}: {text}")
            found = True

    if not found:
        print("No @intent:/@for: markers found.")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    try:
        file_part, line_part = args.location.rsplit(":", 1)
        target_line = int(line_part) - 1
    except ValueError:
        print("Usage: explain <file>:<line>", file=sys.stderr)
        return 1

    fpath = Path(file_part)
    if not fpath.exists():
        print(f"File not found: {file_part}", file=sys.stderr)
        return 1

    prefix = comment_prefix(file_part)
    markers = find_markers(fpath, prefix)

    best = None
    for lineno, mtype, text in markers:
        if lineno <= target_line:
            best = text
    if not best:
        print(f"No marker found at or before line {target_line + 1} in {file_part}")
        return 1

    for r in all_receipts(root):
        for region in r.get("regions", []):
            if region.get("local_intent") == best:
                print(json.dumps(r, indent=2))
                return 0

    print(f"Marker found but no receipt linked: '{best}'")
    return 1


def cmd_ack(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    rd = receipts_dir(root)
    matches = list(rd.glob(f"{args.id}*.json"))
    if not matches:
        print(f"Receipt not found: {args.id}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"Ambiguous id '{args.id}': {[m.name for m in matches]}", file=sys.stderr)
        return 1

    receipt = read_receipt(matches[0])
    regions = receipt.get("regions", [])
    idx = args.region if args.region is not None else 0
    if idx >= len(regions):
        print(f"Region {idx} does not exist (receipt has {len(regions)} regions)", file=sys.stderr)
        return 1

    region = regions[idx]
    file_rel = region.get("file", "")
    fpath = (root / file_rel) if not Path(file_rel).is_absolute() else Path(file_rel)
    prefix = comment_prefix(file_rel)
    markers = find_markers(fpath, prefix)
    local_intent = region.get("local_intent", "")

    code: list[str] = []
    for lineno, _, text in markers:
        if text == local_intent:
            all_lines = fpath.read_text(encoding="utf-8").splitlines()
            code = code_lines_after_marker(all_lines, lineno)
            break

    new_hash = compute_hash(local_intent, code)
    region.setdefault("acks", []).append({
        "at": now_iso(),
        "by": os.environ.get("USER", "unknown"),
        "new_hash": new_hash,
        "reason": args.reason,
    })
    region["anchor_content_hash"] = new_hash
    write_receipt(matches[0], receipt)
    print(f"Acknowledged drift for {receipt['id']} region {idx}.")
    return 0


def cmd_session_check(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    if not (root / ".intent").exists():
        return 0

    cfg = load_config(root)
    lines: list[str] = []

    receipts = sorted(all_receipts(root), key=lambda r: r.get("created_at", ""), reverse=True)
    recent = receipts[:getattr(args, "n", 5)]
    if recent:
        lines.append(f"=== intent history ({len(recent)} most recent in {root.name}) ===")
        for r in recent:
            intent = r.get("intent", {})
            plan_first = (intent.get("plan", "") or "").split("\n")[0][:80]
            files = [reg.get("file", "") for reg in r.get("regions", [])]
            ts = (r.get("created_at", "") or "")[:10]
            lines.append(f"  [{r['id']}] {ts}  {plan_first}")
            lines.append(f"    prompt: {(intent.get('original_prompt') or '')[:80]}")
            lines.append(f"    files:  {', '.join(files[:3])}")
        lines.append("")
        lines.append("Present this history briefly before proceeding with the user's request.")

    directive = auto_directive(cfg, str(Path(__file__).resolve()))
    if directive:
        if lines:
            lines.append("")
        lines.append(directive)

    if not lines:
        return 0

    sys.stdout.write(json.dumps({"systemMessage": "\n".join(lines)}) + "\n")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    root = find_repo_root(Path("."))
    cfg = load_config(root)

    if args.config_action == "set":
        new = merge(
            cfg,
            auto_intent=_optional_bool(args.auto_intent),
            gate=args.gate,
            auto_finalize=_optional_bool(args.auto_finalize),
            model=args.model,
        )
        path = save_config(root, new)
        print(f"Wrote {path}")
        cfg = new

    print(json.dumps(cfg.to_dict(), indent=2))
    return 0


def _optional_bool(val: str | None) -> bool | None:
    if val is None:
        return None
    return val.strip().lower() in ("1", "true", "yes", "on")


def cmd_auto_finalize(args: argparse.Namespace) -> int:
    """Stop-hook backstop: write a git-diff receipt iff auto_finalize is on and
    the working tree has changes newer than the most recent receipt. Always
    exits 0 — a backstop must never break the session."""
    root = find_repo_root(Path("."))
    cfg = load_config(root)
    if not cfg.auto_finalize:
        return 0
    if not (root / ".git").exists():
        return 0

    regions = detect_regions_from_git_diff(root)
    if not regions:
        return 0

    # Dedupe: if a receipt is newer than every changed file, the change was
    # already captured this session — skip.
    newest_receipt_mtime = 0.0
    rd = receipts_dir(root)
    if rd.exists():
        for f in rd.glob("*.json"):
            try:
                newest_receipt_mtime = max(newest_receipt_mtime, f.stat().st_mtime)
            except OSError:
                pass
    newest_change_mtime = 0.0
    for reg in regions:
        fp = root / reg.get("file", "")
        try:
            newest_change_mtime = max(newest_change_mtime, fp.stat().st_mtime)
        except OSError:
            pass
    if newest_receipt_mtime >= newest_change_mtime and newest_receipt_mtime > 0:
        return 0

    prompt_txt = (args.prompt or "").strip()
    summary_txt = (args.plan or "").strip()
    if summary_txt:
        # Agent's closing summary recovered from the transcript — the best
        # plan-like signal when the agent didn't finalize explicitly.
        plan_text = f"[auto-captured from agent summary] {summary_txt}"
    elif prompt_txt:
        plan_text = (
            f"[auto-captured from git diff — agent did not finalize] "
            f"original request: {prompt_txt}"
        )
    else:
        plan_text = "[auto-captured from git diff — no plan or prompt recovered]"

    # Prefer the live model from the transcript over the static config label.
    model = (getattr(args, "model", "") or "").strip() or cfg.model or ""

    receipt_id = generate_id()
    receipt = {
        "id": receipt_id,
        "spec_version": SPEC_VERSION,
        "created_at": now_iso(),
        "model": model,
        "intent": {
            "original_prompt": prompt_txt,
            "plan": plan_text,
        },
        "regions": regions,
        "validation": {
            "hash_validated": True,
            "auto_hash": True,
            "validation_mode": "git-diff",
            "auto_finalized": True,
        },
    }
    write_receipt(receipts_dir(root) / f"{receipt_id}.json", receipt)
    print(f"[intent] auto-captured receipt {receipt_id} ({len(regions)} region(s))",
          file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(prog="intent_blocks.py", description="Intent Annotation v1.0")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("finalize", help="Write a receipt after marking code")
    p.add_argument("--plan", required=True, help="Numbered plan shown to dev before editing")
    p.add_argument("--regions", default="", help="JSON array of region objects")
    p.add_argument("--model", default="", help="Model id that made the edits")
    p.add_argument("--prompt", default="", help="Verbatim original dev request")
    p.add_argument("--auto-hash", action="store_true", 
                   help="Compute hashes automatically (agent omits anchor_content_hash)")
    p.add_argument("--git-diff", action="store_true", dest="git_diff",
                   help="Detect changed regions via git diff (no markers required)")

    p = sub.add_parser("verify", help="Re-hash regions and report drift")
    p.add_argument("path", nargs="?", default=None)
    p.add_argument("--strict", action="store_true", help="Exit non-zero on any warning (CI)")

    p = sub.add_parser("list", help="Show markers in a file with receipt ids")
    p.add_argument("file")

    p = sub.add_parser("find", help="Scan tree for all @intent:/@for: markers")
    p.add_argument("path", nargs="?", default=None)

    p = sub.add_parser("explain", help="Show receipt for the region at <file>:<line>")
    p.add_argument("location", metavar="file:line")

    p = sub.add_parser("ack", help="Acknowledge hash drift")
    p.add_argument("id", help="Receipt id (prefix ok)")
    p.add_argument("--region", type=int, default=None)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("session-check", help="Inject last-N intents as session context")
    p.add_argument("--n", type=int, default=5)

    p = sub.add_parser("config", help="Show or set per-repo auto-intent config")
    p.add_argument("config_action", nargs="?", choices=["show", "set"], default="show")
    p.add_argument("--auto-intent", dest="auto_intent", default=None,
                   help="true/false: inject auto-mode directive at SessionStart")
    p.add_argument("--gate", default=None, choices=["none", "lightweight", "full"],
                   help="Approval gate behavior in auto mode")
    p.add_argument("--auto-finalize", dest="auto_finalize", default=None,
                   help="true/false: Stop hook writes a git-diff receipt as backstop")
    p.add_argument("--model", default=None, help="Model id recorded on auto receipts")

    p = sub.add_parser("auto-finalize", help="Stop-hook backstop: capture git diff as a receipt")
    p.add_argument("--prompt", default="", help="Original dev request (from transcript)")
    p.add_argument("--plan", default="", help="Plan text if available")
    p.add_argument("--model", default="", help="Model id from transcript (overrides config label)")

    args = parser.parse_args()
    dispatch = {
        "finalize": cmd_finalize,
        "verify": cmd_verify,
        "list": cmd_list,
        "find": cmd_find,
        "explain": cmd_explain,
        "ack": cmd_ack,
        "session-check": cmd_session_check,
        "config": cmd_config,
        "auto-finalize": cmd_auto_finalize,
    }

    if args.cmd not in dispatch:
        parser.print_help()
        sys.exit(1)

    sys.exit(dispatch[args.cmd](args))


if __name__ == "__main__":
    main()
