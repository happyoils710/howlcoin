#!/usr/bin/env bash
# Howlcoin deploy + product smoke check (local Mac or on the VPS).
#
# From your Mac (after code is pushed):
#   ./scripts/howl-deploy-verify.sh
#   ./scripts/howl-deploy-verify.sh --ssh howl-vps
#   ./scripts/howl-deploy-verify.sh --api https://howlscan.org --expect 0.6.4
#
# On the VPS:
#   bash /opt/howlcoin/scripts/howl-deploy-verify.sh --local
#
# Exit 0 = all critical checks passed (warnings allowed with --strict off).
# Exit 1 = one or more critical failures.
# Exit 2 = warnings only (bridge offline, sampler missing, etc.)

set -uo pipefail

API="${HOWL_API:-https://howlscan.org}"
EXPECT_VER="${HOWL_EXPECT_VERSION:-0.6.4}"
INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
DATA_DIR="${HOWL_PUBLIC_DATA:-${HOWL_DATA:-/var/lib/howlcoin}}"
SSH_HOST=""
MODE="remote"   # remote | local | ssh
MAX_TIP_AGE="${MAX_TIP_AGE:-7200}"

PASS=0
FAIL=0
WARN=0

usage() {
  cat <<'EOF'
Usage: howl-deploy-verify.sh [options]

  --api URL          Public Howlscan base (default https://howlscan.org)
  --expect VER       Expected software version (default 0.6.4)
  --local            Run on the VPS (systemd + sample file checks)
  --ssh HOST         ssh to HOST and run --local there (e.g. howl-vps)
  --data DIR         Data dir for samples (default /var/lib/howlcoin)
  --dir DIR          Install dir (default /opt/howlcoin)
  --max-tip-age SEC  Health tip age limit (default 7200)
  -h, --help         This help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api) API="$2"; shift 2 ;;
    --expect) EXPECT_VER="$2"; shift 2 ;;
    --local) MODE="local"; shift ;;
    --ssh) MODE="ssh"; SSH_HOST="$2"; shift 2 ;;
    --data) DATA_DIR="$2"; shift 2 ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --max-tip-age) MAX_TIP_AGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

API="${API%/}"

ok()   { PASS=$((PASS+1)); echo "  OK   $*"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL $*"; }
warn() { WARN=$((WARN+1)); echo "  WARN $*"; }

json_get() {
  # json_get '<json>' 'python expr using d'
  local raw="$1"
  local expr="$2"
  python3 -c "
import json,sys
raw=sys.argv[1]
expr=sys.argv[2]
try:
    d=json.loads(raw) if raw else {}
except Exception:
    d={}
print(eval(expr, {'d': d}))
" "$raw" "$expr" 2>/dev/null || true
}

curl_json() {
  local url="$1"
  curl -fsS --max-time 25 -H "Accept: application/json" "$url" 2>/dev/null || true
}

# ---------- SSH mode: re-exec on host ----------
# Note: do NOT use BatchMode — passphrase-protected keys need a prompt / agent.
ssh_run() {
  # Prefer ControlMaster if already connected; allow agent + keychain.
  ssh -o ConnectTimeout=25 -o IdentitiesOnly=no "$@"
}

if [[ "$MODE" == "ssh" ]]; then
  if [[ -z "$SSH_HOST" ]]; then
    echo "FAIL: --ssh requires a host" >&2
    exit 1
  fi
  echo "== Howlcoin deploy verify (ssh → $SSH_HOST) =="
  echo "  (enter key passphrase if prompted)"

  if ! ssh_run -o BatchMode=yes "$SSH_HOST" "echo ok" >/dev/null 2>&1; then
    # Agent empty or BatchMode blocked — try interactive once to unlock key
    if ! ssh_run "$SSH_HOST" "echo ok" >/dev/null; then
      echo "  FAIL ssh to $SSH_HOST (Permission denied or host unreachable)"
      echo "  Fix:"
      echo "    ssh-add --apple-use-keychain ~/.ssh/id_ed25519_howl"
      echo "    ssh $SSH_HOST   # confirm login works"
      echo "    # or run on the VPS:  bash /opt/howlcoin/scripts/howl-deploy-verify.sh --local"
      exit 1
    fi
  fi

  if ssh_run "$SSH_HOST" "test -f ${INSTALL_DIR}/scripts/howl-deploy-verify.sh"; then
    ssh_run "$SSH_HOST" \
      "bash ${INSTALL_DIR}/scripts/howl-deploy-verify.sh --local --api $(printf %q "$API") --expect $(printf %q "$EXPECT_VER") --data $(printf %q "$DATA_DIR") --dir $(printf %q "$INSTALL_DIR") --max-tip-age $(printf %q "$MAX_TIP_AGE")"
    exit $?
  fi

  echo "  WARN remote script missing at ${INSTALL_DIR}/scripts/howl-deploy-verify.sh"
  echo "  Pull latest main on the VPS, then re-run. Mixed remote snapshot:"
  REMOTE_OUT="$(ssh_run "$SSH_HOST" "
    echo GIT:\$(cd ${INSTALL_DIR} 2>/dev/null && git log -1 --oneline || echo missing)
    echo VERSION:\$(cd ${INSTALL_DIR} 2>/dev/null && PYTHONPATH=${INSTALL_DIR} python3 -c 'from howl.config import VERSION;print(VERSION)' 2>/dev/null || echo missing)
    systemctl is-active howlcoin 2>/dev/null || echo howlcoin:down
    systemctl is-active howlcoin-explorer 2>/dev/null || echo explorer:down
    systemctl is-active howl-charts-sampler.timer 2>/dev/null || echo sampler:down
    ls -la ${DATA_DIR}/howl_charts_samples.json 2>/dev/null || echo samples:missing
  " 2>&1 || true)"
  echo "$REMOTE_OUT" | sed 's/^/  | /'
  MODE="remote"
fi

echo "== Howlcoin deploy verify =="
echo "  api=$API expect=$EXPECT_VER mode=$MODE"
echo

# ---------- public API checks ----------
echo "-- Public API --"

HEALTH="$(curl_json "$API/api/public/health?window=20")"
if [[ -z "$HEALTH" ]]; then
  bad "health unreachable: $API/api/public/health"
else
  H_HEIGHT="$(json_get "$HEALTH" "d.get('height','?')")"
  H_AGE="$(json_get "$HEALTH" "d.get('tip_age_seconds','?')")"
  H_STATUS="$(json_get "$HEALTH" "d.get('status','?')")"
  H_VER="$(json_get "$HEALTH" "d.get('version') or ''")"
  ok "health height=$H_HEIGHT status=$H_STATUS tip_age=${H_AGE}s"
  if [[ "$H_AGE" =~ ^[0-9]+$ ]] && [[ "$H_AGE" -gt "$MAX_TIP_AGE" ]]; then
    bad "tip age ${H_AGE}s > ${MAX_TIP_AGE}s (mining/seed stalled?)"
  fi
  if [[ -n "$H_VER" && "$H_VER" != "$EXPECT_VER" ]]; then
    warn "health version=$H_VER (expected $EXPECT_VER) — redeploy if stale"
  elif [[ -n "$H_VER" ]]; then
    ok "reported version $H_VER"
  fi
fi

SUMMARY="$(curl_json "$API/api/public/summary")"
if [[ -z "$SUMMARY" ]]; then
  SUMMARY="$(curl_json "$API/api/summary")"
fi
if [[ -n "$SUMMARY" ]]; then
  S_VER="$(json_get "$SUMMARY" "d.get('version') or d.get('software_version') or ''")"
  S_H="$(json_get "$SUMMARY" "d.get('height','?')")"
  if [[ -n "$S_VER" ]]; then
    if [[ "$S_VER" == "$EXPECT_VER" ]]; then
      ok "summary version $S_VER (height $S_H)"
    else
      warn "summary version=$S_VER expected $EXPECT_VER"
    fi
  else
    ok "summary reachable (height $S_H)"
  fi
else
  warn "summary endpoint not available (non-critical)"
fi

MARKETS="$(curl_json "$API/api/public/markets")"
if [[ -z "$MARKETS" ]]; then
  bad "markets board unreachable"
else
  M_COUNT="$(json_get "$MARKETS" "len(d.get('coins') or [])")"
  M_HOWL="$(json_get "$MARKETS" "int(any(c.get('id')=='howlcoin' for c in (d.get('coins') or [])))")"
  M_SRC="$(json_get "$MARKETS" "d.get('source') or ''")"
  if [[ "${M_COUNT:-0}" -ge 1 ]]; then
    ok "markets count=$M_COUNT source=${M_SRC:-?}"
  else
    bad "markets returned 0 coins"
  fi
  if [[ "${M_HOWL:-0}" == "1" ]]; then
    ok "HOWL present on markets board"
  else
    warn "HOWL missing from markets board"
  fi
fi

CHART="$(curl_json "$API/api/public/chart?id=howlcoin&days=7")"
if [[ -z "$CHART" ]]; then
  bad "howlcoin chart unreachable"
else
  C_N="$(json_get "$CHART" "len(d.get('points') or [])")"
  C_ERR="$(json_get "$CHART" "d.get('error') or ''")"
  C_SRC="$(json_get "$CHART" "d.get('source') or ''")"
  if [[ "${C_N:-0}" -ge 2 ]]; then
    ok "howlcoin chart points=$C_N source=${C_SRC:-?}"
  else
    bad "howlcoin chart empty (${C_ERR:-no points})"
  fi
fi

ETH_CHART="$(curl_json "$API/api/public/chart?id=ethereum&days=7")"
if [[ -n "$ETH_CHART" ]]; then
  E_N="$(json_get "$ETH_CHART" "len(d.get('points') or [])")"
  if [[ "${E_N:-0}" -ge 2 ]]; then
    ok "ethereum chart points=$E_N"
  else
    warn "ethereum chart thin/empty (on-chain feed or samples)"
  fi
fi

BRIDGE="$(curl_json "$API/api/public/bridge")"
if [[ -z "$BRIDGE" ]]; then
  warn "bridge API unreachable"
else
  B_EN="$(json_get "$BRIDGE" "int(bool(d.get('enabled')))")"
  if [[ "${B_EN:-0}" == "1" ]]; then
    ok "Howl Swap bridge enabled"
  else
    warn "Howl Swap offline (intentional until treasury+relayer)"
  fi
fi

SEC="$(curl -fsS --max-time 15 "$API/.well-known/security.txt" 2>/dev/null || true)"
if [[ -n "$SEC" ]] && grep -qi "Contact:" <<<"$SEC"; then
  ok "security.txt present"
else
  warn "security.txt missing or empty"
fi

# ---------- local VPS checks ----------
if [[ "$MODE" == "local" ]]; then
  echo
  echo "-- Local VPS --"

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    GIT_LINE="$(cd "$INSTALL_DIR" && git log -1 --oneline 2>/dev/null || echo missing)"
    ok "git $GIT_LINE"
  else
    warn "install dir missing git: $INSTALL_DIR"
  fi

  PY_VER=""
  if [[ -x /opt/howlcoin-venv/bin/python3 ]]; then
    PY_VER="$(/opt/howlcoin-venv/bin/python3 -c "import sys;sys.path.insert(0,'$INSTALL_DIR');from howl.config import VERSION;print(VERSION)" 2>/dev/null || true)"
  fi
  if [[ -z "$PY_VER" ]]; then
    PY_VER="$(cd "$INSTALL_DIR" 2>/dev/null && PYTHONPATH="$INSTALL_DIR" python3 -c "from howl.config import VERSION;print(VERSION)" 2>/dev/null || true)"
  fi
  if [[ "$PY_VER" == "$EXPECT_VER" ]]; then
    ok "python howl.config.VERSION=$PY_VER"
  elif [[ -n "$PY_VER" ]]; then
    bad "python VERSION=$PY_VER expected $EXPECT_VER — git pull + restart"
  else
    warn "could not import howl.config.VERSION"
  fi

  CLI_VER=""
  if [[ -x /opt/howlcoin-venv/bin/python3 ]]; then
    CLI_VER="$(cd "$INSTALL_DIR" && /opt/howlcoin-venv/bin/python3 -m howl --version 2>/dev/null || true)"
  fi
  if [[ -z "$CLI_VER" ]]; then
    CLI_VER="$(cd "$INSTALL_DIR" 2>/dev/null && PYTHONPATH="$INSTALL_DIR" python3 -m howl --version 2>/dev/null || true)"
  fi
  if [[ -n "$CLI_VER" ]]; then
    if grep -q "$EXPECT_VER" <<<"$CLI_VER"; then
      ok "cli $CLI_VER"
    else
      warn "cli reports: $CLI_VER"
    fi
  fi

  for svc in howlcoin howlcoin-explorer; do
    st="$(systemctl is-active "$svc" 2>/dev/null || echo missing)"
    if [[ "$st" == "active" ]]; then
      ok "systemd $svc active"
    else
      bad "systemd $svc=$st"
    fi
  done

  st="$(systemctl is-active howl-charts-sampler.timer 2>/dev/null || echo missing)"
  if [[ "$st" == "active" ]]; then
    ok "howl-charts-sampler.timer active"
  else
    warn "sampler timer not active — run: bash $INSTALL_DIR/scripts/install-howl-charts-sampler.sh"
  fi

  SAMPLES="$DATA_DIR/howl_charts_samples.json"
  if [[ -f "$SAMPLES" ]]; then
    N_ASSETS="$(python3 -c "import json;d=json.load(open('$SAMPLES'));print(len(d))" 2>/dev/null || echo 0)"
    N_PTS="$(python3 -c "import json;d=json.load(open('$SAMPLES'));print(sum(len(v) for v in d.values() if isinstance(v,list)))" 2>/dev/null || echo 0)"
    ok "samples file assets=$N_ASSETS total_points=$N_PTS ($SAMPLES)"
    if [[ "${N_PTS:-0}" -lt 1 ]]; then
      warn "samples file empty — systemctl start howl-charts-sampler.service"
    fi
  else
    warn "no samples yet at $SAMPLES"
  fi

  if systemctl cat howlcoin 2>/dev/null | grep -q "HOWL_AUTO_MINE=1"; then
    ok "HOWL_AUTO_MINE=1 present on howlcoin unit"
  else
    warn "HOWL_AUTO_MINE not clearly set — tip may stall without miners"
  fi
fi

echo
echo "== Summary: pass=$PASS warn=$WARN fail=$FAIL =="
if [[ "$FAIL" -gt 0 ]]; then
  echo "Result: FAIL"
  exit 1
fi
if [[ "$WARN" -gt 0 ]]; then
  echo "Result: OK with warnings"
  exit 2
fi
echo "Result: OK"
exit 0
