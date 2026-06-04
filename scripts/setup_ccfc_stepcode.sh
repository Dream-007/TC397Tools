#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  setup_ccfc_stepcode.sh            Patch ccfc to launch "stepcode claude"
  setup_ccfc_stepcode.sh --check    Show current patch status
  setup_ccfc_stepcode.sh --restore  Restore the latest non-identical backup

Environment overrides:
  CCFC_BIN=/path/to/ccfc
  CCFC_CLI_JS=/path/to/@hyposomnia/cc-feishu-connector/dist/cli.js
  STEPCODE_BIN=/path/to/stepcode
USAGE
}

MODE="patch"
case "${1:-}" in
  "" ) ;;
  --check ) MODE="check" ;;
  --restore ) MODE="restore" ;;
  -h|--help ) usage; exit 0 ;;
  * ) usage >&2; exit 2 ;;
esac

find_cmd() {
  command -v "$1" 2>/dev/null || true
}

resolve_file() {
  local path="$1"
  if command -v readlink >/dev/null 2>&1; then
    readlink -f "$path"
  else
    local dir
    dir="$(cd "$(dirname "$path")" && pwd)"
    printf '%s/%s\n' "$dir" "$(basename "$path")"
  fi
}

CCFC_BIN="${CCFC_BIN:-$(find_cmd ccfc)}"
STEPCODE_BIN="${STEPCODE_BIN:-$(find_cmd stepcode)}"

if [[ -z "$STEPCODE_BIN" ]]; then
  echo "ERROR: stepcode not found. Set STEPCODE_BIN=/path/to/stepcode." >&2
  exit 1
fi

if [[ -n "${CCFC_CLI_JS:-}" ]]; then
  CLI_JS="$CCFC_CLI_JS"
else
  if [[ -z "$CCFC_BIN" ]]; then
    echo "ERROR: ccfc not found. Set CCFC_BIN=/path/to/ccfc or CCFC_CLI_JS=/path/to/cli.js." >&2
    exit 1
  fi
  CLI_JS="$(resolve_file "$CCFC_BIN")"
fi

if [[ ! -f "$CLI_JS" ]]; then
  echo "ERROR: ccfc cli.js not found: $CLI_JS" >&2
  exit 1
fi

if [[ "$MODE" == "restore" ]]; then
  latest_backup=""
  while IFS= read -r candidate; do
    if [[ -n "$candidate" ]] && ! cmp -s "$candidate" "$CLI_JS"; then
      latest_backup="$candidate"
      break
    fi
  done < <(ls -1t "$CLI_JS".bak-stepcode-* 2>/dev/null || true)
  if [[ -z "$latest_backup" ]]; then
    echo "ERROR: no non-identical backup found for $CLI_JS" >&2
    exit 1
  fi
  cp "$latest_backup" "$CLI_JS"
  echo "Restored ccfc from: $latest_backup"
  node --check "$CLI_JS"
  exit 0
fi

timestamp="$(date '+%Y%m%d-%H%M%S')"

node - "$CLI_JS" "$STEPCODE_BIN" "$MODE" "$timestamp" <<'NODE'
const fs = require("fs");
const [cliPath, stepcodeBin, mode, timestamp] = process.argv.slice(2);
const source = fs.readFileSync(cliPath, "utf8");

const originalLine = '        this.proc = spawn(this.claudeBin, args, {\n';
const patchedBlock = /        process\.stderr\.write\(`Launching Claude via stepcode claude \$\{args\.join\(" "\)\}\\n`\);\n        this\.proc = spawn\("([^"]+)", \["claude", \.\.\.args\], \{\n/;
const replacement =
  `        process.stderr.write(\`Launching Claude via stepcode claude \${args.join(" ")}\\n\`);\n` +
  `        this.proc = spawn(${JSON.stringify(stepcodeBin)}, ["claude", ...args], {\n`;

const patchedMatch = source.match(patchedBlock);

if (mode === "check") {
  if (patchedMatch) {
    console.log(`PATCHED: ccfc launches ${patchedMatch[1]} claude`);
    process.exit(0);
  }
  if (source.includes(originalLine)) {
    console.log("NOT PATCHED: ccfc still launches this.claudeBin");
    process.exit(1);
  }
  console.log("UNKNOWN: launch pattern was not recognized");
  process.exit(2);
}

let next;
if (patchedMatch) {
  if (patchedMatch[1] === stepcodeBin) {
    console.log(`Already patched: ccfc launches ${stepcodeBin} claude`);
    process.exit(0);
  }
  next = source.replace(patchedBlock, replacement);
} else if (source.includes(originalLine)) {
  next = source.replace(originalLine, replacement);
} else {
  console.error("ERROR: unable to find ccfc Claude spawn line.");
  console.error("The installed ccfc version may have changed. Please inspect dist/cli.js manually.");
  process.exit(1);
}

const backup = `${cliPath}.bak-stepcode-${timestamp}`;
fs.copyFileSync(cliPath, backup);
console.log(`Backup: ${backup}`);
fs.writeFileSync(cliPath, next);
console.log(`Patched: ccfc now launches ${stepcodeBin} claude`);
NODE

node --check "$CLI_JS"
echo "ccfc cli.js: $CLI_JS"
echo "stepcode:    $STEPCODE_BIN"
echo "Done. Start ccfc normally, for example:"
echo "ccfc start"
